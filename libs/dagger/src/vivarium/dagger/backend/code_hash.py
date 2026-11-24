"""
=========
code_hash
=========

Hash the code of a Python file or a Jupyter notebook so that an edit that does not change
the code, such as a comment or a re-wrapped line, does not change the hash.

A Python file is hashed by a canonical text of its syntax tree. The text holds each node's
name and its fields, and leaves out what only describes how the code was written. It is
the same on every Python version that dagger supports, so a hash taken on one version
equals the hash of the same file taken on another. Docstrings, annotations, decorators,
the order of imports and string literals are part of the code and change the hash.

A notebook is hashed by its non-empty code cells, in order. Outputs, execution counts,
markdown cells and the notebook's metadata are not read, so a notebook and an executed
copy of it have the same hash. IPython syntax in a cell, such as a ``%magic`` or a
``!shell`` line, is replaced so that the cell parses as Python, and the text of each such
line is also part of the hash. The position of the cell tagged ``parameters`` is part of
the hash, because it decides where the pipeline's values are injected. A semicolon at the
end of a cell's last line is not, because it only hides a displayed value.

A file or a cell that cannot be parsed is hashed by its bytes, and the result says so.
Neither function raises for a file whose content cannot be parsed.

"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeHash:
    """The hash of the code of one file."""

    digest: str
    """The hash. Two files have the same digest only if their code is the same."""
    parsed: bool
    """Whether the digest was taken from parsed code. ``False`` means that the file, or
    at least one code cell of a notebook, could not be parsed and its bytes were hashed
    instead."""


def python_file_hash(path: Path | str) -> CodeHash:
    """Return the hash of the code in the Python file at ``path``.

    The file is parsed as Python, with its encoding declaration or byte-order mark if it
    has one, and hashed by the canonical text of its syntax tree. A file that cannot be
    parsed is hashed by its bytes. The result for one content is computed once per
    process; a file whose bytes changed is parsed again.

    Parameters
    ----------
    path
        The Python file.

    Returns
    -------
        The file's code hash.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    raise NotImplementedError


def notebook_hash(path: Path | str) -> CodeHash:
    """Return the hash of the code in the Jupyter notebook at ``path``.

    The hash covers the notebook's non-empty code cells in order, the text of the IPython
    lines in them, and the position of the cell tagged ``parameters``. A cell that cannot
    be parsed after its IPython syntax is replaced is hashed by its bytes, and so is a
    file that is not a notebook. In both cases the result's ``parsed`` is ``False``. The
    result for one content is computed once per process; a file whose bytes changed is
    read again.

    Parameters
    ----------
    path
        The notebook file.

    Returns
    -------
        The notebook's code hash.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    raise NotImplementedError
