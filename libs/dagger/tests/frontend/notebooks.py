"""Notebook files for tests: source notebooks for papermill to run, and executed notebooks
to read back, written as nbformat 4.5 JSON with the standard library."""

import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

_JUPYTER_DIR_VARIABLES = ("IPYTHONDIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR")
"""The variables that move IPython's and Jupyter's own directories out of HOME."""


def code_cell(
    source: str,
    *,
    tags: Sequence[str] = (),
    execution_count: int | None = None,
    outputs: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return an nbformat 4.5 code cell with ``source``, ``tags``, ``execution_count`` and ``outputs``."""
    return {
        "cell_type": "code",
        "execution_count": execution_count,
        "id": uuid.uuid4().hex[:12],
        "metadata": {"tags": list(tags)} if tags else {},
        "outputs": list(outputs),
        "source": source,
    }


def error_output(ename: str, evalue: str) -> dict[str, Any]:
    """Return a cell output of type ``error`` with ``ename`` and ``evalue``, as Jupyter saves one."""
    return {
        "output_type": "error",
        "ename": ename,
        "evalue": evalue,
        "traceback": [f"{ename}: {evalue}" if evalue else ename],
    }


def write_notebook(path: Path, cells: Sequence[dict[str, Any]]) -> Path:
    """Write ``cells`` to ``path`` as a notebook without kernel metadata, creating its directory."""
    notebook = {"cells": list(cells), "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
    return path


def write_source_notebook(path: Path, parameters: str = "", body: str = "pass") -> Path:
    """Write a notebook of a cell tagged ``parameters`` holding ``parameters``, then a cell holding ``body``."""
    cells = [code_cell(parameters, tags=["parameters"]), code_cell(body)]
    return write_notebook(path, cells)


def recorded_errors(path: Path) -> list[tuple[str, str]]:
    """Return the name and value of each error output in the notebook at ``path``."""
    content = json.loads(path.read_text(encoding="utf-8"))
    return [
        (str(output.get("ename")), str(output.get("evalue")))
        for cell in content["cells"]
        for output in cell.get("outputs", [])
        if output.get("output_type") == "error"
    ]


def broken_env(prefix: Path) -> Path:
    """Create an env at ``prefix`` whose bin/python exits 1 at once, and return ``prefix``."""
    python = prefix / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    return prefix


@contextmanager
def isolated_jupyter_dirs(base: Path) -> Iterator[None]:
    """Point IPython and Jupyter at new empty directories under ``base`` while the block runs."""
    with pytest.MonkeyPatch.context() as patch:
        for variable in _JUPYTER_DIR_VARIABLES:
            directory = base / variable.lower()
            directory.mkdir(parents=True)
            patch.setenv(variable, str(directory))
        yield
