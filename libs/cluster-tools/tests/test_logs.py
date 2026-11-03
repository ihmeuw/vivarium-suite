"""Tests for the main-process log sinks."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from loguru import logger

from vivarium.cluster_tools.core import logs

_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| \d+:\d{2}:\d{2}\.\d{6} \| "
    r"[\w.]+:(?P<function>\w+):\d+ - (?P<message>.*)$"
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def reset_logger() -> Generator[None, None, None]:
    yield
    logger.remove()
    logger.add(sys.stderr)


def _emit() -> None:
    logger.debug("a debug message")
    logger.info("an info message")
    logger.warning("a warning message")


def test_file_logging_writes_short_format_lines(tmp_path: Path) -> None:
    logs.configure_main_process_logging_to_file(tmp_path)
    _emit()

    lines = (tmp_path / "main.log").read_text().splitlines()
    matches = [_LINE.match(line) for line in lines]
    assert all(matches), lines
    assert [(m["function"], m["message"]) for m in matches if m] == [
        ("_emit", "a debug message"),
        ("_emit", "an info message"),
        ("_emit", "a warning message"),
    ]

    records = [
        json.loads(line) for line in (tmp_path / "main.log.json").read_text().splitlines()
    ]
    assert [record["text"].rstrip("\n") for record in records] == lines
    assert [record["record"]["level"]["name"] for record in records] == [
        "DEBUG",
        "INFO",
        "WARNING",
    ]


@pytest.mark.parametrize(
    "verbose, messages",
    [
        (0, ["a warning message"]),
        (1, ["an info message", "a warning message"]),
        (2, ["a debug message", "an info message", "a warning message"]),
    ],
)
def test_terminal_logging_level_follows_verbosity(
    capsys: pytest.CaptureFixture[str], verbose: int, messages: list[str]
) -> None:
    logs.configure_main_process_logging_to_terminal(verbose)
    _emit()

    out = _ANSI.sub("", capsys.readouterr().out)
    matches = [_LINE.match(line) for line in out.splitlines()]
    assert all(matches), out
    assert [m["message"] for m in matches if m] == messages
