"""Tests of the code hash of Python files and notebooks."""

from pathlib import Path

import pytest

# Python files: edits that do not change the code


@pytest.mark.parametrize(
    "edit", ["comment", "blank_lines", "rewrap", "quote_style", "redundant_parentheses"]
)
def test_an_edit_that_does_not_change_the_code_keeps_a_python_files_hash(
    tmp_path: Path, edit: str
) -> None:
    """A comment, added blank lines, a re-wrapped call, a change of quote style, or redundant parentheses around an expression leave the hash of a Python file unchanged, and the result says it was parsed."""


# Python files: edits that change the code


@pytest.mark.parametrize(
    "edit",
    [
        "code",
        "docstring",
        "docstring_reformat",
        "annotation",
        "decorator",
        "import_order",
        "string_literal",
    ],
)
def test_an_edit_of_the_code_changes_a_python_files_hash(tmp_path: Path, edit: str) -> None:
    """A change to a statement, to a docstring (also only re-indenting its text), to an annotation, to a decorator, to the order of two imports, or to a string literal changes the hash of a Python file."""


def test_two_files_with_different_code_have_different_hashes(tmp_path: Path) -> None:
    """Two Python files whose code differs only in one constant (1 and 1.0, or True and 1) have different hashes."""


# Python files: the same hash on every supported Python version


def test_the_hash_of_fixed_sources_matches_the_stored_digests(tmp_path: Path) -> None:
    """For each Python file and notebook in a fixed set of sources stored in the repository, the hash equals the digest stored beside them, which was taken on another Python version; the set includes non-ASCII string literals, f-strings, type annotations, decorators, a match statement and a notebook with IPython lines."""


# Python files that cannot be parsed


def test_a_python_file_with_a_syntax_error_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    """A Python file with a syntax error gets a hash without raising, the result says it was not parsed, and a change to any byte of it changes the hash."""


@pytest.mark.parametrize("marker", ["encoding_declaration", "byte_order_mark"])
def test_a_python_file_with_an_encoding_marker_is_hashed_by_its_code(
    tmp_path: Path, marker: str
) -> None:
    """A Python file that starts with an encoding declaration for latin-1 and holds a latin-1 encoded string literal, or one that starts with a UTF-8 byte-order mark, is parsed, and its hash equals that of a plain UTF-8 file with the same code."""


def test_a_missing_python_file_raises_oserror(tmp_path: Path) -> None:
    """python_file_hash raises OSError for a path that does not exist."""


# Notebooks: what is not part of the hash


def test_a_notebook_and_its_executed_copy_have_the_same_hash(tmp_path: Path) -> None:
    """A notebook and a copy of it that differs in its cell outputs, execution counts, cell and notebook metadata, and kernel name have the same hash, and both results say they were parsed."""


@pytest.mark.parametrize(
    "edit",
    [
        "comment_in_cell",
        "rewrap_in_cell",
        "empty_code_cell",
        "markdown_cell",
        "trailing_semicolon",
    ],
)
def test_an_edit_that_does_not_change_a_notebooks_code_keeps_its_hash(
    tmp_path: Path, edit: str
) -> None:
    """A comment or a re-wrapped line in a code cell, an added empty code cell, an edited or added markdown cell, or a semicolon added at the end of a cell's last line leaves a notebook's hash unchanged."""


# Notebooks: what is part of the hash


@pytest.mark.parametrize(
    "edit",
    ["code_in_cell", "cells_swapped", "magic_line", "shell_line", "parameters_tag_moved"],
)
def test_an_edit_of_a_notebooks_code_changes_its_hash(tmp_path: Path, edit: str) -> None:
    """A code change in a cell, two code cells swapped, a changed %magic line, a changed !shell line, or the parameters tag moved to another code cell changes a notebook's hash."""


@pytest.mark.parametrize(
    "syntax", ["line_magic", "shell_escape", "shell_assignment", "help", "cell_magic"]
)
def test_a_cell_with_ipython_syntax_is_hashed_as_parsed_code(
    tmp_path: Path, syntax: str
) -> None:
    """A notebook whose code cell holds a %line magic, a !shell line, a shell assignment such as files = !ls, a help line such as len?, or a %%time cell magic gets a result that says it was parsed, and a comment added to that cell leaves the hash unchanged."""


# Notebooks that cannot be parsed


def test_a_notebook_cell_that_cannot_be_parsed_still_gives_a_hash(tmp_path: Path) -> None:
    """A notebook with one code cell that is not valid Python, even after its IPython syntax is replaced, gets a hash without raising, the result says it was not parsed, and a change to that cell's text changes the hash."""


def test_a_file_that_is_not_a_notebook_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    """notebook_hash, given a file that is not valid notebook JSON, returns a hash without raising, the result says it was not parsed, and a change to any byte changes the hash."""


def test_a_missing_notebook_raises_oserror(tmp_path: Path) -> None:
    """notebook_hash raises OSError for a path that does not exist."""


# The cache of results


def test_a_file_edited_between_two_calls_gets_a_new_hash(tmp_path: Path) -> None:
    """In one process, a Python file and a notebook that are hashed, then edited with their size and modification time kept the same, then hashed again get the hash of their new code."""


def test_two_paths_with_the_same_content_have_the_same_hash(tmp_path: Path) -> None:
    """Two Python files at different paths with the same bytes, and two notebooks at different paths with the same bytes, have equal hashes."""
