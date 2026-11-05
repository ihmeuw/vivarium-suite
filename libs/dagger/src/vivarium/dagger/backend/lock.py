"""Let only one run at a time work on a pipeline record.

A pipeline record is a build's root, which holds pytask's ``pytask.lock`` and the
``.pytask`` directory. :func:`run_lock` holds an exclusive advisory lock on a lock file
there, :data:`RUN_LOCK_NAME`, for the length of a ``with`` block. A second process that
asks for the lock while the first holds it does not wait: :func:`run_lock` raises
:class:`RunLocked`, which names the lock file and the process that holds it, so the
second run can stop with a plain message.

The lock is :func:`fcntl.flock` on an open file. Closing the file releases it, so it is
released when the block ends, also by an exception, and when the holding process dies,
also by SIGKILL. The file is opened so that a program that the holder starts with
``exec``, such as a :class:`subprocess.Popen` child, does not inherit it. A child that the
holder forks without ``exec`` does inherit it and keeps the lock until that child exits.
On NFS, the client carries out ``flock`` with the server's POSIX locks, which do not
conflict within one process, so two blocks in one process can both hold the lock there.
Use the lock to keep separate processes apart.

The lock file is never deleted, because deleting a lock file while another process waits
on it lets two processes hold locks on different files of the same name. A file that
this module creates can be written by the owner's group whatever the process's umask, so
another account in that group gets a :class:`RunLocked` and not a :class:`PermissionError`.

This module imports only the standard library, so code outside the bridge, such as a
command-line front end, can use it without importing the bridge or Jobmon.
"""

import errno
import fcntl
import os
import pwd
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

RUN_LOCK_NAME = "run.lock"
"""The name of the run lock file in a build's root, beside ``pytask.lock``."""

RUN_REFUSED_KEY = "bridge_refused"
"""The key in pytask's configuration, ``session.config``, under which a build that a
held run lock refused leaves the :class:`RunLocked` that refused it. A build that was
not refused has no value under this key."""

LOCK_FILE_MODE = 0o664
"""The permission bits that :func:`run_lock` gives a lock file that it creates."""

_OPEN_ATTEMPTS = 3


class RunLocked(RuntimeError):
    """Raised when another process holds the run lock on a pipeline record.

    Its message names the lock file and the holder, for example "Another run holds the
    lock on this pipeline record (user jdoe, pid 4242 on host node-17, since
    2026-10-28T10:02:04-07:00). Lock file /path/to/root/run.lock. Wait for that run to
    finish, then run again." When ``holder`` is empty, the parentheses say "holder
    unknown" instead.

    Parameters
    ----------
    path
        Kept as :attr:`path`.
    holder
        Kept as :attr:`holder`.
    """

    path: Path
    """The lock file."""
    holder: str
    """The holder line read from the lock file, as "user <name>, pid <pid> on host
    <host>, since <time>", or an empty string when the holder had not written it yet."""

    def __init__(self, path: Path, holder: str) -> None:
        super().__init__(
            "Another run holds the lock on this pipeline record "
            f"({holder or 'holder unknown'}). Lock file {path}. Wait for that run to "
            "finish, then run again."
        )
        self.path = path
        self.holder = holder


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on the file at ``path`` for the length of the block.

    The file is created if it does not exist, with the mode :data:`LOCK_FILE_MODE` set
    after creation, so that the process's umask does not decide it. An existing file
    keeps its mode. Once the lock is held, the file's content is replaced with one holder
    line for this process, "user <name>, pid <pid> on host <host>, since <time>", where
    the name is that of the account that runs the process (its user id when the account
    has no name), the host is :func:`socket.gethostname`, and the time is the local time
    with its UTC offset in ISO 8601 format, to the second. The line is flushed to the file system before the block
    starts, so a process on another host that is refused can read it.

    The lock is taken without waiting. A process that is refused reads the holder line
    but does not change the file. The lock is released when the block ends, however it
    ends, and the file is left in place with the holder line still in it. That line is
    read only after a refusal, so a line left by a finished run never refuses a later
    one.

    Parameters
    ----------
    path
        The lock file, usually ``<root>/run.lock`` (:data:`RUN_LOCK_NAME`). Its directory
        must exist.

    Yields
    ------
        Nothing. The lock is held while the block runs.

    Raises
    ------
    RunLocked
        If another open file, usually in another process, holds the lock. The
        exception's holder is the first line of the file, without surrounding
        whitespace.
    OSError
        If the file cannot be created or opened for reading and writing, for example
        because its directory does not exist or because another account created it
        without group write permission. Also if ``flock`` fails for a reason other than a
        held lock, such as ``ENOLCK``.
    """
    fd = _open(path)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            # NFS carries out flock with POSIX locks, which can refuse with EACCES.
            if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                raise
            raise RunLocked(path, _read_holder(fd)) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, _holder_line().encode("utf-8"), 0)
        os.fsync(fd)
        yield
    finally:
        os.close(fd)


def _open(path: Path) -> int:
    """Open ``path`` for reading and writing, creating it with LOCK_FILE_MODE if missing."""
    # os.open makes the descriptor non-inheritable (PEP 446), so an exec'd child does not
    # keep the lock.
    attempts = 0
    while True:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, LOCK_FILE_MODE)
        except FileExistsError:
            pass
        else:
            try:
                os.fchmod(fd, LOCK_FILE_MODE)
            except BaseException:
                os.close(fd)
                raise
            return fd
        try:
            return os.open(path, os.O_RDWR)
        # Removed by someone else between the two opens; this module never removes it. A
        # dangling symlink also lands here on every attempt, so the attempts are bounded.
        except FileNotFoundError:
            attempts += 1
            if attempts >= _OPEN_ATTEMPTS:
                raise


def _read_holder(fd: int) -> str:
    """Return the first line of the open lock file without surrounding whitespace."""
    lines = os.pread(fd, 4096, 0).decode("utf-8", errors="replace").splitlines()
    return lines[0].strip() if lines else ""


def _holder_line() -> str:
    """Return "user <name>, pid <pid> on host <host>, since <time>" for this process."""
    uid = os.getuid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    since = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"user {user}, pid {os.getpid()} on host {socket.gethostname()}, since {since}"
