"""A stand-in for Graphviz's ``dot`` program, for tests that draw a graph as an image.

Graphviz is not installed where the tests run. :func:`install_fake_dot` writes an
executable ``dot`` script and puts its folder first on ``PATH``. The script records its
arguments and its standard input, writes its error text, and then either exits with the
status it was given or writes :data:`FAKE_IMAGE` to its standard output.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

FAKE_IMAGE = b"an image drawn by the fake dot\n\x00\xff"
"""The bytes the fake ``dot`` writes to its standard output when it succeeds. They are
not valid UTF-8, so a caller that decodes them as text fails."""

_SCRIPT = r'''#!__PYTHON__
"""Stand in for Graphviz's dot: record the call, then fail or write the image."""

import json
import sys
from pathlib import Path

RECORD = Path(__RECORD__)

args = sys.argv[1:]
with open(RECORD / "calls.jsonl", "a") as log:
    log.write(json.dumps(args) + "\n")
(RECORD / "stdin.txt").write_text(sys.stdin.read())
sys.stderr.write(__STDERR__)
sys.stderr.flush()
if __STATUS__:
    sys.exit(__STATUS__)
sys.stdout.buffer.write(__IMAGE__)
'''


@dataclass(frozen=True)
class FakeDot:
    """The folder where an installed fake ``dot`` records each call."""

    record: Path

    def calls(self) -> list[list[str]]:
        """Return the arguments of each call to the fake, in call order."""
        log = self.record / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line]

    def stdin(self) -> str:
        """Return the standard input of the last call to the fake."""
        return (self.record / "stdin.txt").read_text()


def install_fake_dot(
    directory: Path, monkeypatch: pytest.MonkeyPatch, *, status: int = 0, stderr: str = ""
) -> FakeDot:
    """Write a fake ``dot`` under ``directory`` and put it first on ``PATH``.

    The fake writes ``stderr`` to its standard error, and then exits with ``status`` if
    it is not 0, or else writes :data:`FAKE_IMAGE` to its standard output and exits 0.
    """
    bin_dir = directory / "bin"
    record = directory / "record"
    bin_dir.mkdir(parents=True)
    record.mkdir()
    script = (
        _SCRIPT.replace("__PYTHON__", sys.executable, 1)
        .replace("__RECORD__", repr(str(record)))
        .replace("__STDERR__", repr(stderr))
        .replace("__STATUS__", repr(status))
        .replace("__IMAGE__", repr(FAKE_IMAGE))
    )
    dot = bin_dir / "dot"
    dot.write_text(script)
    dot.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakeDot(record)
