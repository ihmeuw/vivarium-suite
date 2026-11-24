"""Tests of the code part of a step's identity and of its identity node."""

import builtins
import hashlib
import subprocess
import sys
import sysconfig
import textwrap
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from vivarium.dagger.backend.code_hash import python_file_hash
from vivarium.dagger.backend.code_identity import CodeIdentityNode, CodePart, code_part

_STDLIB = sysconfig.get_paths()["stdlib"]
"""The running interpreter's standard library directory."""

_EDITABLE = "<editable>/"
"""The prefix of the name of a file in an editable install's source."""

_EDITABLE_PACKAGE = {
    "edpkg/__init__.py": "",
    "edpkg/core.py": "from edpkg import util\n",
    "edpkg/util.py": "VALUE = 1\n",
}
"""The files of the editable package ``edpkg``, relative to its source directory."""

_EDITABLE_NAMES = {f"{_EDITABLE}{name}" for name in _EDITABLE_PACKAGE}
"""The names of the files of ``edpkg``, relative to the directory that holds the package."""

_EDITABLE_NAMES_IN_PACKAGE = {
    f"{_EDITABLE}{name.removeprefix('edpkg/')}" for name in _EDITABLE_PACKAGE
}
"""The names of the files of ``edpkg``, relative to the package directory itself."""

_FINDER = "__editable___edpkg_0_1_finder"
"""The module name of the setuptools editable finder of ``edpkg``."""


@dataclass(frozen=True)
class _EditableInstall:
    """An editable install of ``edpkg`` under a test's directory."""

    source: Path
    """The directory that holds the package directory ``edpkg``."""
    search_path: list[str]
    """The install's entries of the search path, in order."""
    marker: Path
    """A file that exists only if a file of the install was run."""


def _write(root: Path, files: Mapping[str, str]) -> Path:
    """Write each file's dedented text under ``root``, making directories; return ``root``."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _part(repo: Path, start: str = "main.py", extra: Sequence[Path | str] = ()) -> CodePart:
    """Return the code part of ``repo / start``, searching ``repo`` and then ``extra``."""
    return code_part(repo / start, repo, [str(repo), *(str(entry) for entry in extra)])


def _bytes_digest(path: Path) -> str:
    """Return the digest of a data file: ``bytes:`` and the SHA-256 of its bytes."""
    return "bytes:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_python_digests(part: CodePart, root: Path) -> None:
    """Assert that each listed Python file's digest is its code hash."""
    for name, digest in part.files.items():
        if name.endswith(".py"):
            assert digest == python_file_hash(root / name).digest, name


def _assert_not_established(part: CodePart, file_name: str) -> None:
    """Assert that ``part`` is not established, lists no file and names ``file_name``."""
    assert not part.established
    assert dict(part.files) == {}
    assert part.reason is not None
    assert file_name in part.reason, part.reason


def _editable_install(base: Path, form: str) -> _EditableInstall:
    """Install ``edpkg`` under ``base`` in the editable ``form`` that setuptools writes."""
    source = _write(base / "editable" / "src", _EDITABLE_PACKAGE)
    site = base / "env" / "lib" / "python3" / "site-packages"
    site.mkdir(parents=True)
    marker = base / "ran.txt"
    run_marker = f"open({str(marker)!r}, 'w').close()"
    pth = site / "__editable__.edpkg-0.1.pth"
    if form == "pth_path_entry":
        pth.write_text(f"{source}\nimport os; {run_marker}\n")
        return _EditableInstall(source, [str(site), str(source)], marker)
    (site / f"{_FINDER}.py").write_text(
        "import sys\n"
        f"{run_marker}\n"
        f"MAPPING = {{'edpkg': {str(source / 'edpkg')!r}}}\n"
        "NAMESPACES = {}\n"
        "\n\n"
        "def install():\n"
        "    sys.meta_path.append(None)\n"
    )
    pth.write_text(f"import {_FINDER}; {_FINDER}.install()\n")
    hook = "__editable__.edpkg-0.1.finder.__path_hook__"
    return _EditableInstall(source, [str(site), hook], marker)


# code_part: which files are listed


def test_the_code_part_lists_every_first_party_file_imported_directly_or_indirectly(
    tmp_path: Path,
) -> None:
    """For a starting file that imports a first-party module, which imports another, the code part lists the starting file and both modules by their paths relative to the repository root, and nothing else."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": "import beta\n",
            "beta.py": "VALUE = 1\n",
            "unrelated.py": "VALUE = 2\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert part.reason is None
    assert set(part.files) == {"main.py", "alpha.py", "beta.py"}
    _assert_python_digests(part, repo)


_IMPORT_FORMS: dict[str, tuple[dict[str, str], set[str]]] = {
    "import_inside_a_function": (
        {
            "main.py": """\
                def run():
                    import alpha
                """,
            "alpha.py": "VALUE = 1\n",
        },
        {"main.py", "alpha.py"},
    ),
    "relative_import": (
        {
            "main.py": "import pkg.start\n",
            "pkg/__init__.py": "",
            "pkg/start.py": "from .sibling import VALUE\n",
            "pkg/sibling.py": "VALUE = 1\n",
            "pkg/unused.py": "VALUE = 2\n",
        },
        {"main.py", "pkg/__init__.py", "pkg/start.py", "pkg/sibling.py"},
    ),
    "from_package_import_submodule": (
        {
            "main.py": "from pkg import sub\n",
            "pkg/__init__.py": "",
            "pkg/sub.py": "VALUE = 1\n",
            "pkg/other.py": "VALUE = 2\n",
        },
        {"main.py", "pkg/__init__.py", "pkg/sub.py"},
    ),
    "from_package_import_name": (
        {
            "main.py": "from pkg import NAME\n",
            "pkg/__init__.py": "NAME = 1\n",
            "pkg/sub.py": "VALUE = 2\n",
        },
        {"main.py", "pkg/__init__.py"},
    ),
    "package_init_chain": (
        {
            "main.py": "import top.mid.leaf\n",
            "top/__init__.py": "",
            "top/mid/__init__.py": "",
            "top/mid/leaf.py": "VALUE = 1\n",
            "top/mid/unused.py": "VALUE = 2\n",
        },
        {"main.py", "top/__init__.py", "top/mid/__init__.py", "top/mid/leaf.py"},
    ),
    "namespace_package": (
        {
            "main.py": "import ns.mod\n",
            "ns/mod.py": "VALUE = 1\n",
            "ns/other.py": "VALUE = 2\n",
        },
        {"main.py", "ns/mod.py"},
    ),
}
"""For each form of import, the repository's files and the names the code part lists."""


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
    files, expected = _IMPORT_FORMS[form]
    repo = _write(tmp_path / "repo", files)

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == expected
    _assert_python_digests(part, repo)


_NEVER_RUN: dict[str, dict[str, str]] = {
    "type_checking": {
        "main.py": """\
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                import hidden
            """,
    },
    "typing_type_checking": {
        "main.py": """\
            import typing

            if typing.TYPE_CHECKING:
                import hidden
            """,
    },
    "main_of_a_module": {
        "main.py": "import alpha\n",
        "alpha.py": """\
            VALUE = 1

            if __name__ == "__main__":
                import hidden
            """,
    },
}
"""For each block whose imports never run with the step, the files that hold it."""


@pytest.mark.parametrize(
    "block", ["type_checking", "typing_type_checking", "main_of_a_module"]
)
def test_imports_that_never_run_with_the_step_are_not_followed(
    tmp_path: Path, block: str
) -> None:
    """An import under if TYPE_CHECKING:, under if typing.TYPE_CHECKING:, and an import inside the __main__ block of a module other than the starting file add no file to the code part."""
    files = {**_NEVER_RUN[block], "hidden.py": "VALUE = 1\n"}
    repo = _write(tmp_path / "repo", files)

    part = _part(repo, extra=[_STDLIB])

    assert part.established, part.reason
    assert set(part.files) == set(_NEVER_RUN[block])


def test_the_starting_files_own_main_block_is_followed(tmp_path: Path) -> None:
    """An import inside the starting file's own if __name__ == "__main__": block adds the imported first-party file."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": """\
                if __name__ == "__main__":
                    import alpha
                """,
            "alpha.py": "VALUE = 1\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


def test_a_module_that_raises_when_imported_is_still_listed(tmp_path: Path) -> None:
    """A first-party module whose top level raises an exception is listed, and the analysis does not run it (a module that would write a marker file when imported leaves no marker)."""
    marker = tmp_path / "imported.txt"
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": f"""\
                open({str(marker)!r}, "w").close()
                raise RuntimeError("alpha cannot be imported")
                """,
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}
    assert not marker.exists()


def test_a_data_file_opened_at_import_by_a_path_from_the_modules_location_is_listed(
    tmp_path: Path,
) -> None:
    """A module that, at its top level, opens a YAML file by a path built from Path(__file__).parent and a string literal adds that data file to the code part, and an edit to the data file changes its digest."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path

                import yaml

                SETTINGS = yaml.safe_load(open(Path(__file__).parent / "data" / "settings.yaml"))
                """,
            "data/settings.yaml": "draws: 10\n",
            "data/unused.yaml": "draws: 20\n",
        },
    )
    settings = repo / "data" / "settings.yaml"

    before = _part(repo, extra=[_STDLIB])
    settings.write_text("draws: 11\n")
    after = _part(repo, extra=[_STDLIB])

    assert before.established, before.reason
    assert set(before.files) == {"main.py", "alpha.py", "data/settings.yaml"}
    assert after.files["data/settings.yaml"] == _bytes_digest(settings)
    assert after.files["data/settings.yaml"] != before.files["data/settings.yaml"]
    for name in ("main.py", "alpha.py"):
        assert after.files[name] == before.files[name], name


def test_a_data_file_opened_inside_a_function_is_not_listed(tmp_path: Path) -> None:
    """A module that opens a data file only inside a function body, not at import, adds no data file."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path


                def settings() -> str:
                    return open(Path(__file__).parent / "settings.yaml").read()
                """,
            "settings.yaml": "draws: 10\n",
        },
    )

    part = _part(repo, extra=[_STDLIB])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


def test_installed_code_is_not_listed(tmp_path: Path) -> None:
    """A module imported from a site-packages directory on the search path, a standard library module and a built-in module add no file, and the code part stays established."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": """\
                import json
                import os
                import sys

                import instmod
                import instpkg.sub
                """,
        },
    )
    site = _write(
        tmp_path / "env" / "lib" / "python3" / "site-packages",
        {
            # An installed module's own imports are not followed, so this one, which
            # would make the code part not established, is never reached.
            "instmod.py": "import stray\n",
            "instpkg/__init__.py": "",
            "instpkg/sub.py": "VALUE = 1\n",
        },
    )
    elsewhere = _write(tmp_path / "elsewhere", {"stray.py": "VALUE = 1\n"})

    part = _part(repo, extra=[_STDLIB, site, elsewhere])

    assert part.established, part.reason
    assert set(part.files) == {"main.py"}


def test_a_virtual_environment_inside_the_repository_is_installed_code(
    tmp_path: Path,
) -> None:
    """A module imported from a site-packages directory of a virtual environment that lies inside the repository root is not listed, and the code part stays established."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import venvmod\nimport alpha\n",
            "alpha.py": "VALUE = 1\n",
            ".venv/pyvenv.cfg": "home = /usr/bin\n",
            ".venv/lib/python3/site-packages/venvmod.py": "VALUE = 2\n",
        },
    )
    site = repo / ".venv" / "lib" / "python3" / "site-packages"

    part = _part(repo, extra=[site])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


@pytest.mark.parametrize("form", ["pth_path_entry", "setuptools_finder_mapping"])
def test_the_source_of_an_editable_install_is_first_party(tmp_path: Path, form: str) -> None:
    """A module of an editable install outside the repository, recognised from a .pth file that adds its source directory to the search path or from a setuptools editable finder module whose MAPPING maps its package to its source directory, is listed by a name relative to that source directory, and modules it imports from the same source are listed too."""
    repo = _write(tmp_path / "repo", {"main.py": "import edpkg.core\n"})
    install = _editable_install(tmp_path, form)

    part = _part(repo, extra=install.search_path)

    assert part.established, part.reason
    editable = {name for name in part.files if name != "main.py"}
    assert set(part.files) == {"main.py", *editable}
    if form == "pth_path_entry":
        assert editable == _EDITABLE_NAMES
    else:
        # The design lets a finder's mapped directory, or its parent, be the root.
        assert editable in (_EDITABLE_NAMES, _EDITABLE_NAMES_IN_PACKAGE), editable
    hashes = [python_file_hash(install.source / name) for name in _EDITABLE_PACKAGE]
    assert sorted(part.files[name] for name in editable) == sorted(
        code_hash.digest for code_hash in hashes
    )
    assert not install.marker.exists()


def test_without_a_repository_root_only_editable_sources_are_first_party(
    tmp_path: Path,
) -> None:
    """With no repository root, a starting file in an editable install's source is established and lists its imports, and a starting file in an ordinary directory is not established."""
    install = _editable_install(tmp_path, "pth_path_entry")
    plain = _write(tmp_path / "plain", {"script.py": "import edpkg.core\n"})

    editable = code_part(install.source / "edpkg" / "core.py", None, install.search_path)
    ordinary = code_part(plain / "script.py", None, [str(plain), *install.search_path])

    assert editable.established, editable.reason
    assert set(editable.files) == _EDITABLE_NAMES
    _assert_not_established(ordinary, "script.py")


# code_part: when it is not established


def test_a_starting_file_outside_every_first_party_root_is_not_established(
    tmp_path: Path,
) -> None:
    """A starting file that is neither under the repository root nor in an editable install's source gives a code part that is not established, with a reason that names the file."""
    repo = _write(tmp_path / "repo", {"alpha.py": "VALUE = 1\n"})
    outside = _write(tmp_path / "outside", {"script.py": "import alpha\n"})

    part = code_part(outside / "script.py", repo, [str(outside), str(repo)])

    _assert_not_established(part, "script.py")


def test_an_import_of_a_file_outside_the_roots_that_is_not_installed_is_not_established(
    tmp_path: Path,
) -> None:
    """An import that resolves to a Python file in a directory on the search path that is neither first-party nor installed gives a code part that is not established, with a reason that names that file."""
    repo = _write(
        tmp_path / "repo", {"main.py": "import alpha\n", "alpha.py": "import stray\n"}
    )
    elsewhere = _write(tmp_path / "elsewhere", {"stray.py": "VALUE = 1\n"})

    part = _part(repo, extra=[elsewhere])

    _assert_not_established(part, "stray.py")


def test_an_import_that_resolves_to_nothing_keeps_the_code_part_established(
    tmp_path: Path,
) -> None:
    """An import of a module that exists nowhere on the search path, such as an optional dependency guarded by try/except ImportError, adds no file and keeps the code part established."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": """\
                try:
                    import an_optional_dependency_that_is_nowhere
                except ImportError:
                    an_optional_dependency_that_is_nowhere = None

                import alpha
                """,
            "alpha.py": "VALUE = 1\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


def test_a_first_party_file_that_cannot_be_parsed_is_listed_by_its_bytes(
    tmp_path: Path,
) -> None:
    """A first-party module with a syntax error is listed with a digest that starts with "bytes:", and the analysis follows no imports from it."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import broken\n",
            "broken.py": "import alpha\n\ndef broken(:\n    pass\n",
            "alpha.py": "VALUE = 1\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "broken.py"}
    assert part.files["broken.py"].startswith("bytes:")
    assert part.files["broken.py"] == _bytes_digest(repo / "broken.py")


# code_part: names and digests

_CHECKOUT: dict[str, str] = {
    "main.py": "import pkg.alpha\n",
    "pkg/__init__.py": "",
    "pkg/alpha.py": """\
        from pathlib import Path

        from . import beta

        TABLE = open(Path(__file__).parent / "table.csv").read()
        """,
    "pkg/beta.py": "VALUE = 1\n",
    "pkg/table.csv": "a,b\n1,2\n",
}
"""The files of one checkout of a repository."""


def test_two_checkouts_of_the_same_content_give_the_same_code_part(tmp_path: Path) -> None:
    """The same files copied into two repository roots at different paths give equal code parts."""
    first = _write(tmp_path / "one" / "repo", _CHECKOUT)
    second = _write(tmp_path / "two" / "checkout", _CHECKOUT)

    parts = [_part(root, extra=[_STDLIB]) for root in (first, second)]

    assert parts[0].established, parts[0].reason
    assert set(parts[0].files) == {
        "main.py",
        "pkg/__init__.py",
        "pkg/alpha.py",
        "pkg/beta.py",
        "pkg/table.csv",
    }
    assert parts[0] == parts[1]


def test_a_comment_edit_keeps_the_code_part_and_a_code_edit_changes_it(
    tmp_path: Path,
) -> None:
    """A comment added to an imported module leaves the code part equal, and a change to a statement in it changes the digest of that file only."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                def value() -> int:
                    return 1
                """,
        },
    )
    alpha = repo / "alpha.py"
    original = _part(repo)

    alpha.write_text("# A comment.\ndef value() -> int:\n    return 1  # one\n")
    commented = _part(repo)
    alpha.write_text("def value() -> int:\n    return 2\n")
    edited = _part(repo)

    assert original.established, original.reason
    assert commented == original
    assert set(edited.files) == set(original.files)
    assert edited.files["alpha.py"] != original.files["alpha.py"]
    assert edited.files["main.py"] == original.files["main.py"]


def test_an_import_that_adds_a_file_changes_the_code_part(tmp_path: Path) -> None:
    """Adding an import of another first-party module adds that file to the code part, and removing it removes the file."""
    repo = _write(
        tmp_path / "repo",
        {"main.py": "import alpha\n", "alpha.py": "VALUE = 1\n", "beta.py": "VALUE = 2\n"},
    )
    main = repo / "main.py"
    original = _part(repo)

    main.write_text("import alpha\nimport beta\n")
    added = _part(repo)
    main.write_text("import alpha\n")
    removed = _part(repo)

    assert set(original.files) == {"main.py", "alpha.py"}
    assert set(added.files) == {"main.py", "alpha.py", "beta.py"}
    assert removed == original


def test_an_edit_to_an_unlisted_first_party_file_keeps_the_code_part(tmp_path: Path) -> None:
    """An edit to a first-party file that the step does not import, directly or indirectly, leaves the code part equal."""
    repo = _write(
        tmp_path / "repo",
        {"main.py": "import alpha\n", "alpha.py": "VALUE = 1\n", "beta.py": "VALUE = 2\n"},
    )
    original = _part(repo)

    (repo / "beta.py").write_text("VALUE = 3\nOTHER = 4\n")
    after = _part(repo)

    assert original.established, original.reason
    assert after == original


def test_a_cycle_of_imports_is_followed_once(tmp_path: Path) -> None:
    """Two first-party modules that import each other are each listed once, and the analysis ends."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": "import beta\n",
            "beta.py": "import alpha\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert sorted(part.files) == ["alpha.py", "beta.py", "main.py"]


# CodeIdentityNode


def _node_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a repository whose main module imports another, and put it on sys.path."""
    repo = _write(
        tmp_path / "repo",
        {
            "ci_node_main.py": "import ci_node_alpha\n",
            "ci_node_alpha.py": "def value() -> int:\n    return 1\n",
        },
    ).resolve()
    monkeypatch.syspath_prepend(str(repo))
    return repo


def test_the_node_state_is_equal_for_equal_code_and_differs_after_a_code_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node's state is the same on two calls with no edit, also from a fresh node with the same name, start and root, and changes after a code edit to a listed file."""
    repo = _node_repository(tmp_path, monkeypatch)
    start = repo / "ci_node_main.py"
    node = CodeIdentityNode(name="step::code", start=start, repository_root=repo)

    first = node.state()
    second = node.state()
    fresh = CodeIdentityNode(name="step::code", start=start, repository_root=repo).state()
    (repo / "ci_node_alpha.py").write_text("def value() -> int:\n    return 2\n")
    edited = node.state()

    assert isinstance(first, str) and first
    assert second == first
    assert fresh == first
    assert edited != first


def test_the_node_state_is_the_same_in_a_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child Python process that builds a node with the same start and root reports the same state as this process."""
    repo = _node_repository(tmp_path, monkeypatch)
    start = repo / "ci_node_main.py"
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from vivarium.dagger.backend.code_identity import CodeIdentityNode\n"
        "repo, start = Path(sys.argv[1]), Path(sys.argv[2])\n"
        "sys.path.insert(0, str(repo))\n"
        "node = CodeIdentityNode(name='step::code', start=start, repository_root=repo)\n"
        "print(node.state(), end='')\n"
    )

    local = CodeIdentityNode(name="step::code", start=start, repository_root=repo).state()
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(repo), str(start)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert local
    assert result.stdout == local


def test_a_not_established_node_gives_a_new_non_empty_state_on_every_call(
    tmp_path: Path,
) -> None:
    """A node whose starting file is outside every first-party root returns a non-empty string state that differs on each of two calls."""
    repo = _write(tmp_path / "repo", {"alpha.py": "VALUE = 1\n"}).resolve()
    outside = _write(tmp_path / "outside", {"script.py": "VALUE = 1\n"}).resolve()
    node = CodeIdentityNode(
        name="step::code", start=outside / "script.py", repository_root=repo
    )

    first = node.state()
    second = node.state()

    assert isinstance(first, str) and first
    assert isinstance(second, str) and second
    assert first != second


def test_the_node_state_never_raises(tmp_path: Path) -> None:
    """A node whose starting file has been deleted, and one whose starting file cannot be read, return a non-empty state without raising."""
    repo = _write(tmp_path / "repo", {"deleted.py": "VALUE = 1\n"}).resolve()
    deleted = repo / "deleted.py"
    # A directory with a Python file's name cannot be read as a file, even by root.
    unreadable = repo / "unreadable.py"
    unreadable.mkdir()
    nodes = [
        CodeIdentityNode(name="deleted::code", start=deleted, repository_root=repo),
        CodeIdentityNode(name="unreadable::code", start=unreadable, repository_root=repo),
    ]
    deleted.unlink()

    states = [node.state() for node in nodes]

    for state in states:
        assert isinstance(state, str) and state, state


def test_the_node_protocol(tmp_path: Path) -> None:
    """The node's signature is stable for equal names and differs for different names, load returns the node itself, save raises NotImplementedError, and two nodes with equal name, start and root are equal."""
    repo = _write(
        tmp_path / "repo", {"main.py": "VALUE = 1\n", "other.py": "VALUE = 2\n"}
    ).resolve()
    start = repo / "main.py"
    node = CodeIdentityNode(name="step::code", start=start, repository_root=repo)
    same = CodeIdentityNode(
        name="step::code", start=start, repository_root=repo, attributes={"key": "value"}
    )
    elsewhere = CodeIdentityNode(name="step::code", start=repo / "other.py")
    renamed = CodeIdentityNode(name="other::code", start=start, repository_root=repo)

    assert isinstance(node.signature, str) and node.signature
    assert node.signature == same.signature == elsewhere.signature
    assert node.signature != renamed.signature
    assert node.load() is node
    assert node.load(is_product=True) is node
    with pytest.raises(NotImplementedError):
        node.save("value")
    assert node == same
    assert node != elsewhere
    assert node != renamed


def test_the_analysis_runs_once_per_content_within_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling a node's state three times with no edit reads and parses each listed Python file once, and after an edit to one listed file the next call parses only the edited file again."""
    # A tag unique to this run, so no cache filled by another test holds these contents.
    tag = uuid.uuid4().hex
    labels = ("main", "alpha", "beta")
    repo = _write(
        tmp_path / "repo",
        {
            "ci_cache_main.py": f'import ci_cache_alpha\nMARKER = "main-{tag}"\n',
            "ci_cache_alpha.py": f'import ci_cache_beta\nMARKER = "alpha-{tag}"\n',
            "ci_cache_beta.py": f'MARKER = "beta-{tag}"\nVALUE = 1\n',
        },
    ).resolve()
    monkeypatch.syspath_prepend(str(repo))
    node = CodeIdentityNode(
        name="step::code", start=repo / "ci_cache_main.py", repository_root=repo
    )
    parsed: list[str] = []
    real_compile = builtins.compile

    def counting_compile(source: Any, *args: Any, **kwargs: Any) -> Any:
        """Record which of the repository's files ``source`` is, then compile it."""
        text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
        if isinstance(text, str):
            parsed.extend(label for label in labels if f'"{label}-{tag}"' in text)
        return real_compile(source, *args, **kwargs)

    # ast.parse and code_hash both look compile up in builtins when they call it.
    monkeypatch.setattr(builtins, "compile", counting_compile)

    first = node.state()
    parsed_first = list(parsed)
    parsed.clear()
    node.state()
    node.state()
    parsed_again = list(parsed)
    parsed.clear()
    (repo / "ci_cache_beta.py").write_text(f'MARKER = "beta-{tag}"\nVALUE = 2\n')
    edited = node.state()
    parsed_after_edit = list(parsed)

    assert set(parsed_first) == set(labels), (
        f"parsed {parsed_first}; none means the analysis does not parse with compile or "
        "ast.parse"
    )
    assert parsed_again == []
    assert edited != first
    assert parsed_after_edit and set(parsed_after_edit) == {"beta"}, parsed_after_edit
