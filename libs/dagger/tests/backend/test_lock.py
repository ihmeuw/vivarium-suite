"""Tests for the run lock (``backend.lock``).

Every refusal here holds the lock from a child process, because on NFS ``flock`` locks
do not conflict within one process. The child helpers are in ``tests.backend.lock_helpers``.
"""

import ast
import errno
import inspect
import json
import os
import re
import socket
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from tests.backend.lock_helpers import (
    CHILD_TIMEOUT,
    account_name,
    held_lock,
    let_exit,
    lock_is_free,
)
from vivarium.dagger.backend import lock as lock_module
from vivarium.dagger.backend.lock import LOCK_FILE_MODE, RUN_LOCK_NAME, RunLocked, run_lock

_HOLDER = re.compile(
    r"user (?P<user>.+), pid (?P<pid>\d+) on host (?P<host>.+), since (?P<since>\S+)"
)
"""A holder line, as ``run_lock`` writes it."""

_ISO_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
"""A time in ISO 8601 format to the second, with a UTC offset."""

_EXAMPLE_HOLDER = "user jdoe, pid 4242 on host node-17, since 2026-10-28T10:02:04-07:00"
"""A holder line for the tests of RunLocked's message."""

_EXAMPLE_PATH = Path("/path/to/root") / RUN_LOCK_NAME
"""A lock file for the tests of RunLocked, which never touch it."""

_TRY_RUN_LOCK = """
import sys
from pathlib import Path

from vivarium.dagger.backend.lock import RunLocked, run_lock

try:
    with run_lock(Path(sys.argv[1])):
        print("acquired", flush=True)
except RunLocked:
    print("RunLocked", flush=True)
except OSError as error:
    print(type(error).__name__, flush=True)
"""
"""A child that enters run_lock on the file its first argument names and prints
``acquired``, ``RunLocked`` or the name of the OSError that run_lock raised."""


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def _failing_flock(code: int) -> Callable[[object, int], None]:
    """Return a stand-in for ``fcntl.flock`` that fails with the error number ``code``."""

    def flock(fd: object, operation: int) -> None:
        raise OSError(code, os.strerror(code))

    return flock


def test_run_lock_creates_the_file_and_writes_the_holder_line(tmp_path: Path) -> None:
    """run_lock on a missing path creates it, and inside the block the file holds one line "user <name>, pid <this pid> on host <socket.gethostname()>, since <ISO time with UTC offset>"."""
    path = tmp_path / RUN_LOCK_NAME
    earliest = datetime.now().astimezone().replace(microsecond=0)

    with run_lock(path):
        content = path.read_text(encoding="utf-8")
    latest = datetime.now().astimezone()

    [line] = content.splitlines()
    holder = _HOLDER.fullmatch(line)
    assert holder is not None, content
    assert holder["user"] == account_name()
    assert int(holder["pid"]) == os.getpid()
    assert holder["host"] == socket.gethostname()
    since = holder["since"]
    assert _ISO_SECONDS.fullmatch(since), since
    started = datetime.fromisoformat(since)
    # The offset is the local one at that time, not UTC's unless the host runs on UTC.
    assert started.utcoffset() == started.astimezone().utcoffset(), since
    assert earliest <= started <= latest, (earliest, since, latest)


@pytest.mark.usefixtures("umask_022")
def test_a_created_lock_file_has_mode_0o664_under_umask_022(tmp_path: Path) -> None:
    """With the process umask set to 0o022, the file that run_lock creates has the permission bits LOCK_FILE_MODE, 0o664."""
    path = tmp_path / RUN_LOCK_NAME

    with run_lock(path):
        inside = _mode(path)

    assert LOCK_FILE_MODE == 0o664
    assert inside == LOCK_FILE_MODE
    assert _mode(path) == LOCK_FILE_MODE


def test_an_existing_lock_file_keeps_its_mode(tmp_path: Path) -> None:
    """run_lock on an existing file of this user with mode 0o644 takes the lock and leaves the mode 0o644."""
    path = tmp_path / RUN_LOCK_NAME
    path.touch()
    path.chmod(0o644)

    with run_lock(path):
        held = not lock_is_free(path)
        inside = _mode(path)

    assert held
    assert inside == 0o644
    assert _mode(path) == 0o644


def test_run_lock_is_refused_while_a_child_holds_it(tmp_path: Path) -> None:
    """While a child process holds run_lock on the path, run_lock in this process raises RunLocked whose path is the lock file and whose holder is the child's holder line, naming the child's pid and this host."""
    path = tmp_path / RUN_LOCK_NAME

    with held_lock(path) as child:
        with pytest.raises(RunLocked) as refusal:
            with run_lock(path):
                pass
        first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()

    assert refusal.value.path == path
    assert refusal.value.holder == first_line
    expected = f"pid {child.pid} on host {socket.gethostname()},"
    assert expected in refusal.value.holder, refusal.value.holder


def test_a_refused_run_lock_does_not_change_the_file(tmp_path: Path) -> None:
    """After run_lock is refused while a child holds the lock, the file's bytes are the same as before the refusal."""
    path = tmp_path / RUN_LOCK_NAME

    with held_lock(path):
        before = path.read_bytes()
        with pytest.raises(RunLocked):
            with run_lock(path):
                pass
        after = path.read_bytes()

    assert before
    assert after == before


def test_a_refusal_before_the_holder_line_is_written_has_an_empty_holder(
    tmp_path: Path,
) -> None:
    """While a child holds a bare flock on the file and has written nothing, run_lock raises RunLocked with holder '' and a message saying 'holder unknown'."""
    path = tmp_path / RUN_LOCK_NAME

    with held_lock(path, holder_line=False):
        with pytest.raises(RunLocked) as refusal:
            with run_lock(path):
                pass

    assert refusal.value.holder == ""
    assert "holder unknown" in str(refusal.value), str(refusal.value)


def test_a_refusal_takes_only_the_first_line_of_the_file_as_the_holder(
    tmp_path: Path,
) -> None:
    """While a child holds a bare flock on a file whose first line is a holder line with surrounding whitespace and whose second line is more text, run_lock raises RunLocked whose holder is exactly that first line, stripped."""
    path = tmp_path / RUN_LOCK_NAME
    path.write_text("  user a, pid 1 on host h, since t  \nextra\n", encoding="utf-8")

    with held_lock(path, holder_line=False):
        with pytest.raises(RunLocked) as refusal:
            with run_lock(path):
                pass

    assert refusal.value.holder == "user a, pid 1 on host h, since t"


def test_run_locked_message_names_the_lock_file_and_the_holder() -> None:
    """str(RunLocked(path, holder)) contains the path and the holder line and says to wait for that run to finish and run again."""
    message = str(RunLocked(_EXAMPLE_PATH, _EXAMPLE_HOLDER))

    assert str(_EXAMPLE_PATH) in message, message
    assert _EXAMPLE_HOLDER in message, message
    assert "Wait for that run to finish" in message, message
    assert "run again" in message, message


def test_run_locked_message_says_holder_unknown_for_an_empty_holder() -> None:
    """str(RunLocked(path, '')) contains the path and 'holder unknown'."""
    message = str(RunLocked(_EXAMPLE_PATH, ""))

    assert str(_EXAMPLE_PATH) in message, message
    assert "holder unknown" in message, message


def test_run_locked_is_a_runtime_error_with_path_and_holder() -> None:
    """RunLocked subclasses RuntimeError and keeps its constructor arguments as .path and .holder."""
    error = RunLocked(_EXAMPLE_PATH, _EXAMPLE_HOLDER)

    assert issubclass(RunLocked, RuntimeError)
    assert error.path == _EXAMPLE_PATH
    assert error.holder == _EXAMPLE_HOLDER


def test_the_lock_is_free_after_the_block_returns(tmp_path: Path) -> None:
    """After a run_lock block ends normally, a child process can take the lock."""
    path = tmp_path / RUN_LOCK_NAME

    with run_lock(path):
        held = not lock_is_free(path)

    assert held
    assert lock_is_free(path)


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_the_lock_is_free_after_the_block_raises(
    tmp_path: Path, error: type[BaseException]
) -> None:
    """When a run_lock block raises RuntimeError, KeyboardInterrupt or SystemExit, the exception propagates unchanged and, with this process still alive, a child can take the lock."""
    path = tmp_path / RUN_LOCK_NAME
    raised = error("on purpose")
    held: list[bool] = []

    with pytest.raises(error) as caught:
        with run_lock(path):
            held.append(not lock_is_free(path))
            raise raised

    assert caught.value is raised
    assert held == [True]
    assert lock_is_free(path)


def test_sigkill_of_the_holder_releases_the_lock(tmp_path: Path) -> None:
    """While a child holds run_lock, run_lock here raises RunLocked; after the child is killed with SIGKILL and waited for, run_lock here succeeds."""
    path = tmp_path / RUN_LOCK_NAME
    acquired = False

    with held_lock(path) as child:
        with pytest.raises(RunLocked):
            with run_lock(path):
                pass
        child.kill()
        child.wait(timeout=CHILD_TIMEOUT)
        with run_lock(path):
            acquired = True

    assert acquired


def test_a_holder_line_left_by_a_finished_run_does_not_refuse(tmp_path: Path) -> None:
    """After a child that held run_lock exits, the file still holds its holder line, and run_lock here succeeds and replaces that line with this process's."""
    path = tmp_path / RUN_LOCK_NAME

    with held_lock(path) as child:
        let_exit(child)
        left = path.read_text(encoding="utf-8")

    with run_lock(path):
        replaced = path.read_text(encoding="utf-8")

    assert child.returncode == 0
    assert f"pid {child.pid} on host" in left, left
    assert f"pid {os.getpid()} on host" in replaced, replaced
    assert f"pid {child.pid} on host" not in replaced, replaced


def test_run_lock_replaces_longer_content_with_one_holder_line(tmp_path: Path) -> None:
    """When the lock file holds content that is longer than a holder line and has more than one line, inside a run_lock block the file holds exactly one line, the holder line, and none of the earlier content."""
    path = tmp_path / RUN_LOCK_NAME
    path.write_text("x" * 300 + "\nsecond line\n", encoding="utf-8")

    with run_lock(path):
        content = path.read_text(encoding="utf-8")

    [line] = content.splitlines()
    holder = _HOLDER.fullmatch(line)
    assert holder is not None, content
    # The time pattern rejects leftover "x"s, which the holder pattern's \S+ would take.
    assert _ISO_SECONDS.fullmatch(holder["since"]), content
    assert int(holder["pid"]) == os.getpid()


def test_the_lock_file_is_never_deleted(tmp_path: Path) -> None:
    """After a run_lock block ends normally or by an exception, the lock file still exists with the same inode."""
    path = tmp_path / RUN_LOCK_NAME
    with run_lock(path):
        pass

    # An open handle tells a deleted file from a new one, even if its inode is reused.
    with path.open("rb") as created:
        with pytest.raises(RuntimeError):
            with run_lock(path):
                raise RuntimeError("on purpose")
        with run_lock(path):
            pass
        kept = os.fstat(created.fileno())

    assert kept.st_nlink == 1
    assert os.path.samestat(kept, path.stat())


def test_a_popen_child_started_in_the_block_does_not_keep_the_lock(tmp_path: Path) -> None:
    """A subprocess.Popen child started inside a run_lock block and still running after the block ends does not hold the lock: another child can take it."""
    path = tmp_path / RUN_LOCK_NAME

    with run_lock(path):
        # With close_fds=False, only the lock file's close-on-exec flag keeps it from
        # the child.
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            close_fds=False,
        )
    with sleeper:
        try:
            running = sleeper.poll() is None
            free = lock_is_free(path)
        finally:
            sleeper.kill()

    assert running
    assert free


def test_run_lock_raises_os_error_when_the_directory_is_missing(tmp_path: Path) -> None:
    """run_lock on a path whose directory does not exist raises an OSError (FileNotFoundError), not RunLocked."""
    path = tmp_path / "missing" / RUN_LOCK_NAME

    with pytest.raises(FileNotFoundError) as raised:
        with run_lock(path):
            pass

    assert not isinstance(raised.value, RunLocked)
    assert not path.parent.exists()


def test_run_lock_on_a_dangling_symlink_raises_file_not_found_error(tmp_path: Path) -> None:
    """run_lock on a lock file that is a symlink to a path in a missing directory raises FileNotFoundError, not RunLocked, within CHILD_TIMEOUT (run in a child so that an endless open loop fails the test), and leaves the symlink dangling."""
    path = tmp_path / RUN_LOCK_NAME
    target = tmp_path / "missing" / "target"
    path.symlink_to(target)

    # subprocess.run kills the child and raises TimeoutExpired if run_lock never returns.
    child = subprocess.run(
        [sys.executable, "-c", _TRY_RUN_LOCK, str(path)],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        check=False,
    )

    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["FileNotFoundError"], child.stdout + child.stderr
    assert path.is_symlink()
    assert path.readlink() == target
    assert not target.parent.exists()


@pytest.mark.parametrize("code", [errno.EACCES, errno.EAGAIN], ids=["EACCES", "EAGAIN"])
def test_a_flock_error_that_means_the_lock_is_held_raises_run_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """When fcntl.flock fails with EACCES or EAGAIN, the errors that mean another open file holds the lock, run_lock raises RunLocked with the lock file's path and the holder line already in the file, and leaves the file unchanged."""
    path = tmp_path / RUN_LOCK_NAME
    path.write_text(f"{_EXAMPLE_HOLDER}\n", encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(lock_module.fcntl, "flock", _failing_flock(code))

    with pytest.raises(RunLocked) as refusal:
        with run_lock(path):
            pass

    assert refusal.value.path == path
    assert refusal.value.holder == _EXAMPLE_HOLDER
    assert path.read_bytes() == before


def test_a_flock_error_for_another_reason_propagates_as_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When fcntl.flock fails with another error number, such as ENOLCK, run_lock lets that OSError propagate and does not raise RunLocked."""
    path = tmp_path / RUN_LOCK_NAME
    path.write_text(f"{_EXAMPLE_HOLDER}\n", encoding="utf-8")
    monkeypatch.setattr(lock_module.fcntl, "flock", _failing_flock(errno.ENOLCK))

    # RunLocked is not an OSError, so a refusal would escape pytest.raises.
    with pytest.raises(OSError) as raised:
        with run_lock(path):
            pass

    assert raised.value.errno == errno.ENOLCK


def test_the_lock_module_imports_only_the_standard_library() -> None:
    """Every module that backend/lock.py itself imports is in sys.stdlib_module_names, and importing it in a fresh interpreter imports neither jobmon nor vivarium.dagger.bridge.execute."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(lock_module))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module is not None, ast.dump(node)
            imported.add(node.module)
    code = (
        "import json, sys\n"
        "import vivarium.dagger.backend.lock\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )

    child = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
        check=False,
    )

    assert imported, "backend/lock.py imports nothing"
    roots = {name.split(".")[0] for name in imported}
    assert roots <= sys.stdlib_module_names, roots - sys.stdlib_module_names
    assert child.returncode == 0, child.stderr
    loaded: list[str] = json.loads(child.stdout)
    assert [name for name in loaded if name.split(".")[0] == "jobmon"] == []
    assert "vivarium.dagger.bridge.execute" not in loaded
