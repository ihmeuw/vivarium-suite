"""Helpers that hold or try the run lock from child processes.

On NFS, ``flock`` locks do not conflict within one process, so the tests hold and check
the run lock from child Python processes. This module imports only the standard library,
so the tests of the backend and of the bridge can both use it.
"""

import os
import pwd
import select
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

CHILD_TIMEOUT = 60
"""The most seconds that a test waits for a child process that holds or tries the lock."""

_HOLD_LOCK = """
import fcntl
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
if sys.argv[2] == "run_lock":
    from vivarium.dagger.backend.lock import run_lock

    with run_lock(path):
        print("ready", flush=True)
        sys.stdin.read()
else:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("ready", flush=True)
    sys.stdin.read()
"""
"""A child that holds the lock on the file its first argument names until its standard
input closes, with ``run_lock`` or, when its second argument is ``flock``, with a bare
``flock`` that writes nothing."""

_TRY_LOCK = """
import errno
import fcntl
import os
import sys

fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as error:
    if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
        raise
    print("held", flush=True)
else:
    fcntl.flock(fd, fcntl.LOCK_UN)
    print("free", flush=True)
finally:
    os.close(fd)
"""
"""A child that tries the lock on the existing file its first argument names, prints
``free`` or ``held``, and releases the lock if it took it. A ``flock`` error other than
one that means the lock is held ends the child with a traceback."""


def account_name() -> str:
    """Return the name of the account that runs this process, or its user id."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def let_exit(child: "subprocess.Popen[str]") -> None:
    """Let a child of :func:`held_lock` exit by closing its standard input; wait for it."""
    assert child.stdin is not None
    child.stdin.close()
    child.wait(timeout=CHILD_TIMEOUT)


@contextmanager
def held_lock(path: Path, *, holder_line: bool = True) -> Iterator["subprocess.Popen[str]"]:
    """Hold the run lock on ``path`` from a child Python process; yield the child.

    The child takes the lock with ``run_lock``, or with ``holder_line`` false with a bare
    ``fcntl.flock`` that writes nothing to the file, prints ``ready`` and waits. The block
    starts once the child has printed ``ready``. On exit the child is killed if it is still
    running, and waited for. A child holds the lock because on NFS ``flock`` locks do not
    conflict within one process. If the child does not print ``ready``, for example
    because it was refused, the block does not start and AssertionError is raised with
    the child's standard error.
    """
    how = "run_lock" if holder_line else "flock"
    with subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(path), how],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            _wait_until_ready(child, path)
            yield child
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=CHILD_TIMEOUT)


def _wait_until_ready(child: "subprocess.Popen[str]", path: Path) -> None:
    """Wait until ``child`` prints ``ready``; raise AssertionError if it does not."""
    assert child.stdout is not None
    readable, _, _ = select.select([child.stdout], [], [], CHILD_TIMEOUT)
    if readable and child.stdout.readline().strip() == "ready":
        return
    child.kill()
    _, stderr = child.communicate(timeout=CHILD_TIMEOUT)
    raise AssertionError(f"The child did not take the lock on {path}:\n{stderr}")


def lock_is_free(path: Path) -> bool:
    """Return whether a child Python process can take the lock on ``path`` without waiting.

    The child opens the existing file for reading and writing, tries an exclusive
    non-blocking ``flock``, and releases it at once. It does not create or change the file.
    If the child gives no answer, for example because the file does not exist or ``flock``
    failed for a reason other than a held lock, AssertionError is raised with the child's
    standard error.
    """
    child = subprocess.run(
        [sys.executable, "-c", _TRY_LOCK, str(path)],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        check=False,
    )
    answer = child.stdout.strip()
    if child.returncode != 0 or answer not in ("free", "held"):
        raise AssertionError(
            f"The child could not try the lock on {path} (exit {child.returncode}):\n"
            f"{child.stderr}"
        )
    return answer == "free"
