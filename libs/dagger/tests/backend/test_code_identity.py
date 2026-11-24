"""Tests of the code part of a step's identity and of its identity node."""

from pathlib import Path

import pytest

# code_part: which files are listed


def test_the_code_part_lists_every_first_party_file_imported_directly_or_indirectly(
    tmp_path: Path,
) -> None:
    """For a starting file that imports a first-party module, which imports another, the code part lists the starting file and both modules by their paths relative to the repository root, and nothing else."""


@pytest.mark.parametrize(
    "form",
    [
        "import_inside_a_function",
        "relative_import",
        "from_package_import_submodule",
        "from_package_import_name",
        "package_init_chain",
        "namespace_package",
    ],
)
def test_each_form_of_import_resolves_to_its_files(tmp_path: Path, form: str) -> None:
    """An import inside a function, a relative import, from pkg import submodule, from pkg import name (a name defined in the package), the __init__ files of every package level imported, and a namespace package without an __init__ each add exactly the files that the import loads."""


@pytest.mark.parametrize(
    "block", ["type_checking", "typing_type_checking", "main_of_a_module"]
)
def test_imports_that_never_run_with_the_step_are_not_followed(
    tmp_path: Path, block: str
) -> None:
    """An import under if TYPE_CHECKING:, under if typing.TYPE_CHECKING:, and an import inside the __main__ block of a module other than the starting file add no file to the code part."""


def test_the_starting_files_own_main_block_is_followed(tmp_path: Path) -> None:
    """An import inside the starting file's own if __name__ == "__main__": block adds the imported first-party file."""


def test_a_module_that_raises_when_imported_is_still_listed(tmp_path: Path) -> None:
    """A first-party module whose top level raises an exception is listed, and the analysis does not run it (a module that would write a marker file when imported leaves no marker)."""


def test_a_data_file_opened_at_import_by_a_path_from_the_modules_location_is_listed(
    tmp_path: Path,
) -> None:
    """A module that, at its top level, opens a YAML file by a path built from Path(__file__).parent and a string literal adds that data file to the code part, and an edit to the data file changes its digest."""


def test_a_data_file_opened_inside_a_function_is_not_listed(tmp_path: Path) -> None:
    """A module that opens a data file only inside a function body, not at import, adds no data file."""


def test_installed_code_is_not_listed(tmp_path: Path) -> None:
    """A module imported from a site-packages directory on the search path, a standard library module and a built-in module add no file, and the code part stays established."""


def test_a_virtual_environment_inside_the_repository_is_installed_code(
    tmp_path: Path,
) -> None:
    """A module imported from a site-packages directory of a virtual environment that lies inside the repository root is not listed, and the code part stays established."""


@pytest.mark.parametrize("form", ["pth_path_entry", "setuptools_finder_mapping"])
def test_the_source_of_an_editable_install_is_first_party(tmp_path: Path, form: str) -> None:
    """A module of an editable install outside the repository, recognised from a .pth file that adds its source directory to the search path or from a setuptools editable finder module whose MAPPING maps its package to its source directory, is listed by a name relative to that source directory, and modules it imports from the same source are listed too."""


def test_without_a_repository_root_only_editable_sources_are_first_party(
    tmp_path: Path,
) -> None:
    """With no repository root, a starting file in an editable install's source is established and lists its imports, and a starting file in an ordinary directory is not established."""


# code_part: when it is not established


def test_a_starting_file_outside_every_first_party_root_is_not_established(
    tmp_path: Path,
) -> None:
    """A starting file that is neither under the repository root nor in an editable install's source gives a code part that is not established, with a reason that names the file."""


def test_an_import_of_a_file_outside_the_roots_that_is_not_installed_is_not_established(
    tmp_path: Path,
) -> None:
    """An import that resolves to a Python file in a directory on the search path that is neither first-party nor installed gives a code part that is not established, with a reason that names that file."""


def test_an_import_that_resolves_to_nothing_keeps_the_code_part_established(
    tmp_path: Path,
) -> None:
    """An import of a module that exists nowhere on the search path, such as an optional dependency guarded by try/except ImportError, adds no file and keeps the code part established."""


def test_a_first_party_file_that_cannot_be_parsed_is_listed_by_its_bytes(
    tmp_path: Path,
) -> None:
    """A first-party module with a syntax error is listed with a digest that starts with "bytes:", and the analysis follows no imports from it."""


# code_part: names and digests


def test_two_checkouts_of_the_same_content_give_the_same_code_part(tmp_path: Path) -> None:
    """The same files copied into two repository roots at different paths give equal code parts."""


def test_a_comment_edit_keeps_the_code_part_and_a_code_edit_changes_it(
    tmp_path: Path,
) -> None:
    """A comment added to an imported module leaves the code part equal, and a change to a statement in it changes the digest of that file only."""


def test_an_import_that_adds_a_file_changes_the_code_part(tmp_path: Path) -> None:
    """Adding an import of another first-party module adds that file to the code part, and removing it removes the file."""


def test_an_edit_to_an_unlisted_first_party_file_keeps_the_code_part(tmp_path: Path) -> None:
    """An edit to a first-party file that the step does not import, directly or indirectly, leaves the code part equal."""


def test_a_cycle_of_imports_is_followed_once(tmp_path: Path) -> None:
    """Two first-party modules that import each other are each listed once, and the analysis ends."""


# CodeIdentityNode


def test_the_node_state_is_equal_for_equal_code_and_differs_after_a_code_edit(
    tmp_path: Path,
) -> None:
    """A node's state is the same on two calls with no edit, also from a fresh node with the same name, start and root, and changes after a code edit to a listed file."""


def test_the_node_state_is_the_same_in_a_fresh_process(tmp_path: Path) -> None:
    """A child Python process that builds a node with the same start and root reports the same state as this process."""


def test_a_not_established_node_gives_a_new_non_empty_state_on_every_call(
    tmp_path: Path,
) -> None:
    """A node whose starting file is outside every first-party root returns a non-empty string state that differs on each of two calls."""


def test_the_node_state_never_raises(tmp_path: Path) -> None:
    """A node whose starting file has been deleted, and one whose starting file cannot be read, return a non-empty state without raising."""


def test_the_node_protocol(tmp_path: Path) -> None:
    """The node's signature is stable for equal names and differs for different names, load returns the node itself, save raises NotImplementedError, and two nodes with equal name, start and root are equal."""


def test_the_analysis_runs_once_per_content_within_a_process(tmp_path: Path) -> None:
    """Calling a node's state three times with no edit reads and parses each listed Python file once, and after an edit to one listed file the next call parses only the edited file again."""
