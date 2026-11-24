"""Tests of the code part of a step's identity and of its identity node."""

import ast
import builtins
import hashlib
import importlib.machinery
import io
import os
import re
import subprocess
import sys
import sysconfig
import textwrap
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from vivarium.dagger.backend import code_identity as code_identity_module
from vivarium.dagger.backend.code_hash import python_file_hash
from vivarium.dagger.backend.code_identity import CodeIdentityNode, CodePart, code_part

_STDLIB = sysconfig.get_paths()["stdlib"]
"""The running interpreter's standard library directory."""

_EDITABLE = "<editable>/"
"""The prefix of the name of a file in an editable install's source."""

_EDITABLE_PACKAGE = {
    "__init__.py": "",
    "core.py": "from {package} import util\n",
    "util.py": "VALUE = 1\n",
}
"""The files of the editable package, relative to its package directory, where
``{package}`` stands for the package's import name."""

_EDITABLE_FORMS = [
    "pth_path_entry",
    "finder_mapping",
    "annotated_finder_mapping",
    "dotted_finder_mapping",
    "annotated_dotted_finder_mapping",
    "mismatched_finder_mapping",
    "mismatched_dotted_finder_mapping",
]
"""The forms of an editable install: a .pth entry of the source directory, or a setuptools
finder whose ``MAPPING`` is a plain or an annotated assignment and maps a top-level or a
dotted package name, either to a directory whose last components are the name's
components or, for a mismatched form, to one whose last components differ from them."""

_FINDER = "__editable___edpkg_0_1_finder"
"""The module name of the setuptools editable finder that the tests write."""

_HOOK = "__editable__.edpkg-0.1.finder.__path_hook__"
"""The search path entry of that finder's path hook, which is not a directory."""


@dataclass(frozen=True)
class _EditableInstall:
    """An editable install of a package under a test's directory."""

    source: Path
    """The source directory that the names of the package's files are relative to."""
    package: str
    """The package's import name."""
    package_dir: Path
    """The package's directory, which the install maps the package's name to."""
    search_path: list[str]
    """The install's entries of the search path, in order."""
    marker: Path
    """A file that exists only if a file of the install was run."""
    repository_files: dict[str, str] = field(default_factory=dict)
    """Files that the repository must hold for the package's name to import."""

    @property
    def files(self) -> list[Path]:
        """Return the package's files."""
        return [self.package_dir / name for name in _EDITABLE_PACKAGE]

    @property
    def names(self) -> set[str]:
        """Return the names that a code part gives the package's files."""
        return {_EDITABLE + path.relative_to(self.source).as_posix() for path in self.files}


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


def _site(base: Path) -> Path:
    """Make and return a site-packages directory under ``base``."""
    site = base / "env" / "lib" / "python3" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return site


def _run_marker(marker: Path) -> str:
    """Return a statement that creates ``marker`` when it runs."""
    return f"open({str(marker)!r}, 'w').close()"


def _write_finder(
    site: Path, mapping: Mapping[str, Path], marker: Path, annotated: bool = False
) -> str:
    """Write a setuptools editable finder of ``mapping`` and its .pth; return its hook."""
    mapping_type, namespaces_type = (
        (": dict[str, str]", ": dict[str, list[str]]") if annotated else ("", "")
    )
    entries = {name: str(path) for name, path in mapping.items()}
    (site / f"{_FINDER}.py").write_text(
        "import sys\n"
        f"{_run_marker(marker)}\n"
        f"MAPPING{mapping_type} = {entries!r}\n"
        f"NAMESPACES{namespaces_type} = {{}}\n"
        "\n\n"
        "def install():\n"
        "    sys.meta_path.append(None)\n"
    )
    pth = site / "__editable__.edpkg-0.1.pth"
    pth.write_text(f"import {_FINDER}; {_FINDER}.install()\n")
    return _HOOK


def _watch_opens(
    monkeypatch: pytest.MonkeyPatch, files: Iterable[Path], on_open: Callable[[Path], None]
) -> None:
    """Call ``on_open`` with each of ``files`` just before any open function opens it."""
    watched = {path.resolve() for path in files}
    real_open = io.open
    real_open_code = io.open_code
    real_os_open = os.open

    def check(file: object) -> None:
        """Call ``on_open`` if ``file`` names a watched file."""
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).resolve()
            if path in watched:
                on_open(path)

    def watching_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        """Check ``file``, then open it with io.open."""
        check(file)
        return real_open(file, *args, **kwargs)

    def watching_open_code(path: str) -> Any:
        """Check ``path``, then open it with io.open_code."""
        check(path)
        return real_open_code(path)

    def watching_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        """Check ``path``, then open it with os.open."""
        check(path)
        return real_os_open(path, *args, **kwargs)

    # pathlib opens through io.open, which is the same function as builtins.open but is
    # looked up on the io module.
    monkeypatch.setattr(builtins, "open", watching_open)
    monkeypatch.setattr(io, "open", watching_open)
    monkeypatch.setattr(io, "open_code", watching_open_code)
    monkeypatch.setattr(os, "open", watching_os_open)


def _editable_install(base: Path, form: str) -> _EditableInstall:
    """Install a package under ``base`` in the editable ``form`` that setuptools writes."""
    dotted = "dotted" in form
    package = "outer.edpkg" if dotted else "edpkg"
    source = base / "editable" / "src"
    package_dir = source.joinpath(*package.split("."))
    if form.startswith("mismatched"):
        # For the dotted name only the first component differs, so a root found by
        # stripping the key's components without comparing each of them is wrong.
        package_dir = source / "lib" / "edpkg" if dotted else source / "edpkg_source"
        source = package_dir.parent
    _write(
        package_dir,
        {name: text.format(package=package) for name, text in _EDITABLE_PACKAGE.items()},
    )
    site = _site(base)
    marker = base / "ran.txt"
    if form == "pth_path_entry":
        pth = site / "__editable__.edpkg-0.1.pth"
        pth.write_text(f"{source}\nimport os; {_run_marker(marker)}\n")
        return _EditableInstall(
            source, package, package_dir, [str(site), str(source)], marker
        )
    hook = _write_finder(site, {package: package_dir}, marker, annotated="annotated" in form)
    # The parent of a dotted name is an ordinary package of the repository, which the
    # search path finds; its submodule is then found only through the finder's MAPPING.
    parent = {"outer/__init__.py": ""} if dotted else {}
    return _EditableInstall(source, package, package_dir, [str(site), hook], marker, parent)


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


_STAR_PACKAGE = {
    "main.py": "from pkg import *\n",
    "pkg/sub.py": "VALUE = 1\n",
    "pkg/other.py": "VALUE = 2\n",
    "pkg/unused.py": "VALUE = 3\n",
}
"""A repository whose starting file star-imports the package ``pkg``, without the
package's ``__init__.py``."""

_STAR_ALL: dict[str, tuple[str, set[str]]] = {
    "literal_list": (
        '__all__ = ["sub", "other", "NAME"]\nNAME = 1\n',
        {"pkg/sub.py", "pkg/other.py"},
    ),
    "literal_tuple": (
        '__all__ = ("sub", "other", "NAME")\nNAME = 1\n',
        {"pkg/sub.py", "pkg/other.py"},
    ),
    "missing": ("NAME = 1\n", set()),
}
"""For each ``__all__`` of ``pkg/__init__.py`` that a code part can read, the text of the
``__init__.py`` and the submodules that ``from pkg import *`` adds."""

_NON_LITERAL_ALL = {
    "concatenation": '__all__ = ["sub"] + ["other"]\n',
    "comprehension": '__all__ = [name for name in ("sub", "other")]\n',
    "call": '__all__ = list(("sub", "other"))\n',
    "non_string_element": 'OTHER = "other"\n__all__ = ["sub", OTHER]\n',
    "string": '__all__ = "sub"\n',
}
"""Texts of a package ``__init__.py`` whose ``__all__`` is not a literal list or tuple of
strings."""


@pytest.mark.parametrize("form", list(_STAR_ALL))
def test_a_star_import_of_a_package_lists_the_submodules_that_its_literal_all_names(
    tmp_path: Path, form: str
) -> None:
    """from pkg import * on a first-party package lists each submodule that a literal __all__ list or tuple names, and no file for a name that is not a submodule, and lists no submodule when the package has no __all__."""
    init, submodules = _STAR_ALL[form]
    repo = _write(tmp_path / "repo", {**_STAR_PACKAGE, "pkg/__init__.py": init})

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "pkg/__init__.py", *submodules}


@pytest.mark.parametrize("form", list(_NON_LITERAL_ALL))
def test_a_star_import_of_a_first_party_package_with_a_non_literal_all_is_not_established(
    tmp_path: Path, form: str
) -> None:
    """from pkg import * on a first-party package whose __all__ is a concatenation, a comprehension, a call, a list with a name in it, or a bare string gives a code part that is not established, with a reason that names the package's __init__.py."""
    repo = _write(
        tmp_path / "repo", {**_STAR_PACKAGE, "pkg/__init__.py": _NON_LITERAL_ALL[form]}
    )

    part = _part(repo)

    _assert_not_established(part, "__init__.py")


@pytest.mark.parametrize("form", [*_STAR_ALL, *_NON_LITERAL_ALL])
def test_a_star_import_of_an_installed_package_adds_nothing(
    tmp_path: Path, form: str
) -> None:
    """from pkg import * on an installed package adds no file and keeps the code part established, whether the package's __all__ is literal, missing or not literal."""
    init = _STAR_ALL[form][0] if form in _STAR_ALL else _NON_LITERAL_ALL[form]
    repo = _write(tmp_path / "repo", {"main.py": "from instpkg import *\n"})
    site = _write(
        _site(tmp_path),
        {
            "instpkg/__init__.py": init,
            "instpkg/sub.py": "VALUE = 1\n",
            "instpkg/other.py": "VALUE = 2\n",
        },
    )

    part = _part(repo, extra=[site])

    assert part.established, part.reason
    assert set(part.files) == {"main.py"}


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


def test_a_data_file_opened_in_a_class_body_is_listed(tmp_path: Path) -> None:
    """A data file opened in the body of a module-level class, or of a class nested in it, is listed, because a class body runs at import, while one opened in a method is not."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path


                class Settings:
                    TEXT = open(Path(__file__).parent / "class_body.txt").read()

                    class Inner:
                        TEXT = open(Path(__file__).parent / "nested_class_body.txt").read()

                    def reload(self) -> str:
                        return open(Path(__file__).parent / "method.txt").read()
                """,
            "class_body.txt": "one\n",
            "nested_class_body.txt": "two\n",
            "method.txt": "three\n",
        },
    )

    part = _part(repo, extra=[_STDLIB])

    assert part.established, part.reason
    assert set(part.files) == {
        "main.py",
        "alpha.py",
        "class_body.txt",
        "nested_class_body.txt",
    }
    assert part.files["class_body.txt"] == _bytes_digest(repo / "class_body.txt")


_DATA_CALLS: dict[str, str] = {
    "positional_argument": 'TABLE = read_csv(Path(__file__).parent / "t.csv")\n',
    "method_call": 'TABLE = pd.read_csv(Path(__file__).parent / "t.csv")\n',
    "keyword_argument": 'ARRAY = np.load(file=Path(__file__).parent / "t.csv")\n',
    "expression_statement": 'yaml.safe_load(Path(__file__).parent / "t.csv")\n',
    "nested_call": 'CONFIG = json.loads(read_text(Path(__file__).parent / "t.csv"))\n',
    "module_level_name": (
        'HERE = Path(__file__).parent\nTABLE = pd.read_csv(HERE / "t.csv")\n'
    ),
    "annotated_module_level_name": (
        'HERE: Path = Path(__file__).parent\nTABLE = pd.read_csv(HERE / "t.csv")\n'
    ),
    "chained_module_level_name": (
        'HERE = THERE = Path(__file__).parent\nTABLE = pd.read_csv(HERE / "t.csv")\n'
    ),
    "class_body": (
        'class Tables:\n    TABLE = pd.read_csv(Path(__file__).parent / "t.csv")\n'
    ),
}
"""Top-level calls of any function with a path built from ``__file__`` to ``t.csv``."""


@pytest.mark.parametrize("call", list(_DATA_CALLS))
def test_a_data_file_passed_to_any_call_at_import_is_listed(
    tmp_path: Path, call: str
) -> None:
    """A call of any function at a module's or a class body's top level, with the path of an existing file built from __file__ as a positional or keyword argument (pd.read_csv, np.load, yaml.safe_load, a call nested in another, a path through a module-level name bound once by a top-level plain, annotated or chained assignment), lists that file by its bytes, and the module itself keeps its code hash."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": f"from pathlib import Path\n\n{_DATA_CALLS[call]}",
            "t.csv": "a,b\n1,2\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py", "t.csv"}
    assert part.files["t.csv"] == _bytes_digest(repo / "t.csv")
    _assert_python_digests(part, repo)


_NO_DATA_CALLS: dict[str, str] = {
    "missing_file": 'TABLE = pd.read_csv(Path(__file__).parent / "missing.csv")\n',
    "directory": 'TABLE = pd.read_csv(Path(__file__).parent / "data")\n',
    "function_body": (
        'def load():\n    return pd.read_csv(Path(__file__).parent / "t.csv")\n'
    ),
    "async_function_body": (
        'async def load():\n    return pd.read_csv(Path(__file__).parent / "t.csv")\n'
    ),
    "lambda_body": 'LOAD = lambda: pd.read_csv(Path(__file__).parent / "t.csv")\n',
    "method_body": (
        "class Tables:\n"
        "    def load(self):\n"
        '        return pd.read_csv(Path(__file__).parent / "t.csv")\n'
    ),
    "name_bound_only_in_if": (
        'if True:\n    HERE = Path(__file__).parent\nTABLE = pd.read_csv(HERE / "t.csv")\n'
    ),
    "name_read_in_a_function_run_at_import": (
        'DATA = Path(__file__).parent / "t.csv"\n'
        "\n\n"
        "def load():\n"
        "    return pd.read_csv(DATA)\n"
        "\n\n"
        "TABLE = load()\n"
    ),
    "class_local_name": (
        "class Tables:\n"
        '    DATA = Path(__file__).parent / "t.csv"\n'
        "    TABLE = read(DATA)\n"
    ),
}
"""Calls with a path built from ``__file__`` that name no existing file or do not run at
import, and two that do read an existing file at import but that the analysis accepts it
does not see: a module-level path read only in a function that a top-level call runs, and
a path through a name local to a class body."""


@pytest.mark.parametrize("call", list(_NO_DATA_CALLS))
def test_a_call_that_does_not_read_an_existing_file_at_import_lists_no_data_file(
    tmp_path: Path, call: str
) -> None:
    """A call whose path names a missing file or a directory, a call in the body of a function, an async function, a lambda or a method, a call whose path goes through a name bound only inside an if block rather than at the module's top level, and, as accepted limits of the detection, a module-level path read only inside a function that runs at import and a path through a class-local name, add no data file."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": f"from pathlib import Path\n\n{_NO_DATA_CALLS[call]}",
            "t.csv": "a,b\n1,2\n",
            "data/inner.csv": "a,b\n3,4\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


_REBINDINGS: dict[str, tuple[str, bool]] = {
    "bound_once": ("", True),
    "function_parameter": ("def load(HERE):\n    return HERE\n", True),
    "function_local": (
        'def load():\n    HERE = Path("/elsewhere")\n    return HERE\n',
        True,
    ),
    "async_function_local": ('async def load():\n    HERE = Path("/elsewhere")\n', True),
    "method_local": (
        'class Loader:\n    def load(self):\n        HERE = Path("/elsewhere")\n',
        True,
    ),
    "lambda_parameter": ("LOAD = lambda HERE: HERE\n", True),
    "comprehension_target": ("NAMES = [HERE for HERE in range(3)]\n", True),
    "augmented_assignment": ('HERE /= "sub"\n', False),
    "second_assignment": ('HERE = Path("/elsewhere")\n', False),
    "annotated_assignment": ('HERE: Path = Path("/elsewhere")\n', False),
    "for_target": ('for HERE in [Path("/elsewhere")]:\n    pass\n', False),
    "with_target": ('with nullcontext(Path("/elsewhere")) as HERE:\n    pass\n', False),
    "walrus": ('(HERE := Path("/elsewhere"))\n', False),
    "tuple_unpacking": ('HERE, OTHER = Path("/elsewhere"), 1\n', False),
    "starred_target": ("*HERE, OTHER = [1, 2]\n", False),
    "import_alias": ("import os as HERE\n", False),
    "def_name": ("def HERE():\n    pass\n", False),
    "class_name": ("class HERE:\n    pass\n", False),
    "del": ("del HERE\n", False),
    "assignment_in_if": ('if OTHER:\n    HERE = Path("/elsewhere")\n', False),
    "assignment_in_else": (
        'if OTHER:\n    pass\nelse:\n    HERE = Path("/elsewhere")\n',
        False,
    ),
    "assignment_in_for": ('for _ in range(1):\n    HERE = Path("/elsewhere")\n', False),
    "assignment_in_while": (
        'while OTHER:\n    HERE = Path("/elsewhere")\n    break\n',
        False,
    ),
    "assignment_in_with": ('with nullcontext():\n    HERE = Path("/elsewhere")\n', False),
    "assignment_in_try": (
        'try:\n    HERE = Path("/elsewhere")\nexcept OSError:\n    pass\n',
        False,
    ),
    "assignment_in_finally": (
        'try:\n    pass\nfinally:\n    HERE = Path("/elsewhere")\n',
        False,
    ),
    "assignment_in_match": (
        'match OTHER:\n    case True:\n        HERE = Path("/elsewhere")\n',
        False,
    ),
    "global_in_function": (
        'def reset():\n    global HERE\n    HERE = Path("/elsewhere")\n',
        False,
    ),
    "global_in_class": (
        'class Reset:\n    global HERE\n    HERE = Path("/elsewhere")\n',
        False,
    ),
}
"""Ways to bind the name ``HERE`` after ``HERE = Path(__file__).parent``, each with whether
the data file is still listed: it is when ``HERE`` is bound only once in module scope,
also when the other binding is local to a function, a lambda or a comprehension, and it is
not when the other binding runs in module scope or a function or class declares ``HERE``
global."""


@pytest.mark.parametrize("rebinding", list(_REBINDINGS))
def test_a_name_bound_more_than_once_adds_no_data_file(
    tmp_path: Path, rebinding: str
) -> None:
    """A data path through a module-level name lists its file when the name is bound once in module scope, also when a function, an async function, a method, a lambda or a comprehension binds the same name as a parameter or a local, and adds nothing when the name is also bound in module scope (a second plain or annotated assignment, an augmented assignment, a for target, a with target, a walrus, tuple or starred unpacking, an import alias, a def or class name, del, or an assignment nested in if, else, for, while, with, try, finally or match) or a function or class declares it global."""
    binding, listed = _REBINDINGS[rebinding]
    module = (
        "from contextlib import nullcontext\n"
        "from pathlib import Path\n"
        "\n"
        "OTHER = True\n"
        "HERE = Path(__file__).parent\n"
        f"{binding}"
        'TABLE = pd.read_csv(HERE / "t.csv")\n'
    )
    repo = _write(
        tmp_path / "repo",
        {"main.py": "import alpha\n", "alpha.py": module, "t.csv": "a,b\n1,2\n"},
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py", *(["t.csv"] if listed else [])}


def test_a_name_bound_again_after_its_use_adds_no_data_file(tmp_path: Path) -> None:
    """A module-level name that is bound again anywhere in the module, also after the call that uses it in a data path, adds no data file."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path

                HERE = Path(__file__).parent
                TABLE = pd.read_csv(HERE / "t.csv")
                HERE /= "sub"
                """,
            "t.csv": "a,b\n1,2\n",
        },
    )

    part = _part(repo)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


_COMPILED_MODULES = {
    "tagged_extension": "fastmod" + importlib.machinery.EXTENSION_SUFFIXES[0],
    "plain_extension": "fastmod" + importlib.machinery.EXTENSION_SUFFIXES[-1],
    "sourceless_bytecode": "fastmod" + importlib.machinery.BYTECODE_SUFFIXES[0],
}
"""The file names of a compiled module ``fastmod``: an extension module with the
interpreter's tagged suffix and with its plain suffix (``.so`` or ``.pyd``), and a
``.pyc`` without a source file."""


@pytest.mark.parametrize("kind", list(_COMPILED_MODULES))
def test_a_compiled_first_party_module_is_listed_by_its_bytes(
    tmp_path: Path, kind: str
) -> None:
    """A first-party module that resolves to an extension module or to a sourceless .pyc is listed with the bytes digest of the file, and rebuilding the file changes its digest."""
    name = _COMPILED_MODULES[kind]
    repo = _write(tmp_path / "repo", {"main.py": "import fastmod\n"})
    compiled = repo / name
    compiled.write_bytes(b"\x7fELF\x02\x01\x01 the first build")

    before = _part(repo)
    compiled.write_bytes(b"\x7fELF\x02\x01\x01 the second build")
    after = _part(repo)

    assert before.established, before.reason
    assert set(before.files) == {"main.py", name}
    assert after.files[name] == _bytes_digest(compiled)
    assert after.files[name] != before.files[name]


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


def test_a_data_path_to_an_installed_file_adds_nothing(tmp_path: Path) -> None:
    """A path built from __file__ that resolves to an existing data file in a site-packages directory adds no file, and the code part stays established."""
    site = _write(_site(tmp_path), {"instpkg/d.csv": "a,b\n1,2\n"})
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path

                TABLE = pd.read_csv(
                    Path(__file__).parent
                    / ".."
                    / "env"
                    / "lib"
                    / "python3"
                    / "site-packages"
                    / "instpkg"
                    / "d.csv"
                )
                """,
        },
    )
    assert (repo / ".." / "env" / "lib" / "python3" / "site-packages").samefile(site)

    part = _part(repo, extra=[site])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "alpha.py"}


@pytest.mark.parametrize("form", _EDITABLE_FORMS)
def test_the_source_of_an_editable_install_is_first_party(tmp_path: Path, form: str) -> None:
    """A module of an editable install outside the repository, recognised from a .pth file that adds its source directory to the search path or from a setuptools editable finder module whose plain or annotated MAPPING maps its top-level or dotted package name to the package's directory, is listed by a name relative to a root, and modules it imports from the same source are listed too; for a .pth entry the root is the source directory, and for a MAPPING key it is the mapped directory with the key's components removed from its end when its last components equal them, and the mapped directory's parent when they do not."""
    install = _editable_install(tmp_path, form)
    repo = _write(
        tmp_path / "repo",
        {"main.py": f"import {install.package}.core\n", **install.repository_files},
    )

    part = _part(repo, extra=install.search_path)

    assert part.established, part.reason
    assert set(part.files) == {"main.py", *install.repository_files, *install.names}
    for path in install.files:
        name = _EDITABLE + path.relative_to(install.source).as_posix()
        assert part.files[name] == python_file_hash(path).digest, name
    assert not install.marker.exists()


def test_without_a_repository_root_only_editable_sources_are_first_party(
    tmp_path: Path,
) -> None:
    """With no repository root, a starting file in an editable install's source is established and lists its imports, and a starting file in an ordinary directory is not established."""
    install = _editable_install(tmp_path, "pth_path_entry")
    plain = _write(tmp_path / "plain", {"script.py": "import edpkg.core\n"})

    editable = code_part(install.package_dir / "core.py", None, install.search_path)
    ordinary = code_part(plain / "script.py", None, [str(plain), *install.search_path])

    assert editable.established, editable.reason
    assert set(editable.files) == install.names
    _assert_not_established(ordinary, "script.py")


@pytest.mark.parametrize("position", ["before_the_finder", "after_the_finder"])
def test_a_package_on_the_search_path_wins_over_an_editable_finders_mapping(
    tmp_path: Path, position: str
) -> None:
    """A first-party package found anywhere on the search path, before or after the entries of an editable finder, is listed from there and not from the directory that the finder's MAPPING maps the same name to, because the interpreter consults the finder only when the search path finds nothing."""
    install = _editable_install(tmp_path, "finder_mapping")
    prefix = "" if position == "before_the_finder" else "lib/"
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import edpkg.core\n",
            f"{prefix}edpkg/__init__.py": "",
            f"{prefix}edpkg/core.py": "VALUE = 2\n",
        },
    )

    part = _part(repo, extra=[*install.search_path, repo / "lib"])

    assert part.established, part.reason
    assert set(part.files) == {
        "main.py",
        f"{prefix}edpkg/__init__.py",
        f"{prefix}edpkg/core.py",
    }


def test_a_mapping_to_a_directory_without_an_init_is_a_namespace_package(
    tmp_path: Path,
) -> None:
    """An editable finder's MAPPING entry whose directory has no __init__.py resolves as a namespace package at that directory, so import ns.mod lists ns/mod.py there and no other file of it."""
    source = _write(
        tmp_path / "editable" / "src",
        {"ns/mod.py": "VALUE = 1\n", "ns/other.py": "VALUE = 2\n"},
    )
    marker = tmp_path / "ran.txt"
    site = _site(tmp_path)
    hook = _write_finder(site, {"ns": source / "ns"}, marker)
    repo = _write(tmp_path / "repo", {"main.py": "import ns.mod\n"})

    part = _part(repo, extra=[site, hook])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", f"{_EDITABLE}ns/mod.py"}
    assert not marker.exists()


_EXTEND_PATH = {
    "import_pkgutil": (
        "import pkgutil\n\n__path__ = pkgutil.extend_path(__path__, __name__)\n"
    ),
    "from_pkgutil_import": (
        "from pkgutil import extend_path\n\n__path__ = extend_path(__path__, __name__)\n"
    ),
    "dunder_import": '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n',
}
"""The spellings of a package ``__init__.py`` that extends its ``__path__`` with
:func:`pkgutil.extend_path`."""


@pytest.mark.parametrize("spelling", list(_EXTEND_PATH))
def test_a_package_that_extends_its_path_resolves_a_submodule_in_a_later_portion(
    tmp_path: Path, spelling: str
) -> None:
    """A package whose __init__ assigns __path__ from pkgutil.extend_path, through import pkgutil, from pkgutil import extend_path or __import__("pkgutil"), resolves a submodule that only a portion of it in a later search directory holds, from the first such portion in search-path order."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import nsp.portion\n",
            "nsp/__init__.py": _EXTEND_PATH[spelling],
            "second/nsp/portion.py": "VALUE = 1\n",
            "third/nsp/portion.py": "VALUE = 2\n",
        },
    )

    part = _part(repo, extra=[repo / "second", repo / "third", _STDLIB])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "nsp/__init__.py", "second/nsp/portion.py"}


def test_a_package_that_does_not_extend_its_path_resolves_only_its_own_directory(
    tmp_path: Path,
) -> None:
    """A package whose __init__ does not call pkgutil.extend_path does not resolve a submodule that only another search directory's directory of the same name holds."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import nsp.portion\n",
            "nsp/__init__.py": "",
            "second/nsp/portion.py": "VALUE = 1\n",
        },
    )

    part = _part(repo, extra=[repo / "second", _STDLIB])

    assert part.established, part.reason
    assert set(part.files) == {"main.py", "nsp/__init__.py"}


_PORTION = {
    "engine/src/vns/__init__.py": _EXTEND_PATH["import_pkgutil"],
    "dagger/src/vns/dag/__init__.py": "",
    "dagger/src/vns/dag/step.py": "VALUE = 1\n",
    "dagger/src/vns/dag/unused.py": "VALUE = 2\n",
}
"""A repository laid out as this one: the package ``vns``, whose ``__init__.py`` calls
:func:`pkgutil.extend_path`, in ``engine/src``, and the subpackage ``vns.dag``, which holds
the starting file, in a portion of ``vns`` without an ``__init__.py`` in ``dagger/src``."""


@pytest.mark.parametrize("order", ["extended_package_first", "portion_first"])
def test_a_starting_module_in_a_portion_of_an_extended_package_lists_the_packages_init(
    tmp_path: Path, order: str
) -> None:
    """A starting file in a portion of a package that extends its path, with the package's own directory and the portion's directory both on the search path in either order, lists the extend_path __init__.py from the other directory and the __init__.py of its own subpackage, by the module's dotted name."""
    repo = _write(tmp_path / "repo", _PORTION)
    entries = [repo / "engine" / "src", repo / "dagger" / "src"]
    if order == "portion_first":
        entries.reverse()

    part = _part(repo, start="dagger/src/vns/dag/step.py", extra=[*entries, _STDLIB])

    assert part.established, part.reason
    assert set(part.files) == {
        "dagger/src/vns/dag/step.py",
        "dagger/src/vns/dag/__init__.py",
        "engine/src/vns/__init__.py",
    }


def test_an_init_at_or_above_the_search_path_entry_of_the_starting_file_is_not_listed(
    tmp_path: Path,
) -> None:
    """For a starting file under a search-path entry, an __init__.py in the entry's own directory or in a directory above it, which importing the starting module by its dotted name does not run, is not listed, while its package's __init__.py is."""
    repo = _write(
        tmp_path / "repo",
        {
            "outer/__init__.py": "",
            "outer/src/__init__.py": "",
            "outer/src/pkg/__init__.py": "",
            "outer/src/pkg/start.py": "VALUE = 1\n",
        },
    )

    part = _part(repo, start="outer/src/pkg/start.py", extra=[repo / "outer" / "src"])

    assert part.established, part.reason
    assert set(part.files) == {"outer/src/pkg/start.py", "outer/src/pkg/__init__.py"}


_LIBRARY = {
    "vns/lib/__init__.py": "",
    "vns/lib/core.py": "from vns.lib import helper\n",
    "vns/lib/helper.py": "VALUE = 1\n",
}
"""The files of the library ``vns.lib``, relative to its source directory, which, like
a vivarium library's, holds no ``vns/__init__.py``."""


def _library_install(base: Path, form: str) -> list[str]:
    """Install ``vns.lib`` editable in ``form`` under ``base``; return its search path."""
    source = _write(base / "editable" / "src", _LIBRARY)
    site = _site(base)
    if form == "pth_path_entry":
        (site / "__editable__.vns_lib-0.1.pth").write_text(f"{source}\n")
        return [str(site), str(source)]
    hook = _write_finder(site, {"vns.lib": source / "vns" / "lib"}, base / "ran.txt")
    return [str(site), hook]


def test_the_two_editable_forms_name_a_library_under_an_extended_package_alike(
    tmp_path: Path,
) -> None:
    """With a package vns whose __init__ calls pkgutil.extend_path, as vivarium's does, an editable library vns.lib installed by a .pth entry of its source directory and by a finder MAPPING of the dotted name vns.lib to its directory each resolve to the library's source and give the same names."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import vns.lib.core\n",
            "vns/__init__.py": _EXTEND_PATH["import_pkgutil"],
        },
    )
    forms = ["pth_path_entry", "dotted_finder_mapping"]

    parts = {
        form: _part(repo, extra=[_STDLIB, *_library_install(tmp_path / form, form)])
        for form in forms
    }

    expected = {"main.py", "vns/__init__.py", *(_EDITABLE + name for name in _LIBRARY)}
    for form in forms:
        assert parts[form].established, (form, parts[form].reason)
        assert set(parts[form].files) == expected, form
    assert parts["pth_path_entry"] == parts["dotted_finder_mapping"]


def test_an_editable_source_inside_the_repository_is_named_relative_to_the_repository(
    tmp_path: Path,
) -> None:
    """An editable install whose source directory lies inside the repository root, installed by a .pth entry of the source directory and by a finder MAPPING of the package to its directory, names the package's files by their paths relative to the repository root without the <editable>/ prefix, and the two forms give equal code parts."""
    package = {
        f"editable/src/edpkg/{name}": text.format(package="edpkg")
        for name, text in _EDITABLE_PACKAGE.items()
    }
    repo = _write(tmp_path / "repo", {"main.py": "import edpkg.core\n", **package})
    source = repo / "editable" / "src"
    pth_site = _site(tmp_path / "pth_path_entry")
    (pth_site / "__editable__.edpkg-0.1.pth").write_text(f"{source}\n")
    finder_site = _site(tmp_path / "finder_mapping")
    hook = _write_finder(finder_site, {"edpkg": source / "edpkg"}, tmp_path / "ran.txt")

    parts = {
        "pth_path_entry": _part(repo, extra=[pth_site, source]),
        "finder_mapping": _part(repo, extra=[finder_site, hook]),
    }

    for form, part in parts.items():
        assert part.established, (form, part.reason)
        assert set(part.files) == {"main.py", *package}, form
    assert parts["pth_path_entry"] == parts["finder_mapping"]


# code_part: when it is not established


def test_a_starting_file_outside_every_first_party_root_is_not_established(
    tmp_path: Path,
) -> None:
    """A starting file that is neither under the repository root nor in an editable install's source gives a code part that is not established, with a reason that names the file."""
    repo = _write(tmp_path / "repo", {"alpha.py": "VALUE = 1\n"})
    outside = _write(tmp_path / "outside", {"script.py": "import alpha\n"})

    part = code_part(outside / "script.py", repo, [str(outside), str(repo)])

    _assert_not_established(part, "script.py")


def test_an_installed_starting_file_is_not_established_as_installed_code(
    tmp_path: Path,
) -> None:
    """A starting file in a site-packages directory gives a code part that is not established, with a reason that names the file and says it is installed code, not that it is outside the repository root."""
    repo = _write(tmp_path / "repo", {"main.py": "VALUE = 1\n"})
    site = _write(_site(tmp_path), {"instmod.py": "VALUE = 2\n"})

    part = code_part(site / "instmod.py", repo, [str(repo), str(site)])

    _assert_not_established(part, "instmod.py")
    assert part.reason is not None
    assert "is installed code" in part.reason, part.reason
    assert "outside the repository root" not in part.reason, part.reason


def test_an_imported_file_that_cannot_be_read_is_not_established(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An imported first-party file whose read raises PermissionError gives a code part that is not established, with a reason that names the file and says it cannot be read."""
    repo = _write(tmp_path / "repo", {"main.py": "import alpha\n", "alpha.py": "VALUE = 1\n"})

    def refuse(path: Path) -> None:
        """Raise the error that reading a file without permission raises."""
        raise PermissionError(13, "Permission denied", str(path))

    _watch_opens(monkeypatch, [repo / "alpha.py"], refuse)
    part = _part(repo)

    _assert_not_established(part, "alpha.py")
    assert part.reason is not None
    assert re.search(r"(cannot|could not|can't) be read", part.reason), part.reason


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


def test_a_data_path_to_a_file_outside_the_roots_that_is_not_installed_is_not_established(
    tmp_path: Path,
) -> None:
    """A path built from __file__ at import that resolves to an existing data file outside every first-party root and not installed gives a code part that is not established, with a reason that names that file."""
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": "import alpha\n",
            "alpha.py": """\
                from pathlib import Path

                TABLE = pd.read_csv(Path(__file__).parent / ".." / "outside" / "foreign.csv")
                """,
        },
    )
    _write(tmp_path / "outside", {"foreign.csv": "a,b\n1,2\n"})

    part = _part(repo)

    _assert_not_established(part, "foreign.csv")


def test_two_files_that_nested_editable_roots_give_one_name_are_not_established(
    tmp_path: Path,
) -> None:
    """With two .pth entries of editable sources, one nested in the other, two different files that would get the same name in the code part, each relative to its own root, give a code part that is not established, with a reason that names both files."""
    outer = (tmp_path / "editable" / "src").resolve()
    inner = outer / "inner"
    reads_table = (
        "from pathlib import Path\n\n"
        'TABLE = open(Path(__file__).parent / "t.csv").read()\n'
    )
    _write(
        outer,
        {
            "outer_mod.py": reads_table,
            "t.csv": "a\n1\n",
            "inner/inner_mod.py": reads_table,
            "inner/t.csv": "a\n2\n",
        },
    )
    site = _site(tmp_path)
    (site / "__editable__.nested-0.1.pth").write_text(f"{inner}\n{outer}\n")
    repo = _write(tmp_path / "repo", {"main.py": "import outer_mod\nimport inner_mod\n"})

    part = _part(repo, extra=[site, inner, outer, _STDLIB])

    _assert_not_established(part, str(outer / "t.csv"))
    assert part.reason is not None
    assert str(inner / "t.csv") in part.reason, part.reason


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


# code_part: reading and parsing


def test_each_python_file_is_read_once_for_its_hash_and_its_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Computing a code part of contents not seen before opens each listed Python file exactly once, for its code hash and its import scan together."""
    # A tag unique to this run, so no cache filled by another test holds these contents.
    tag = uuid.uuid4().hex
    repo = _write(
        tmp_path / "repo",
        {
            "main.py": f'import alpha\nMARKER = "{tag}"\n',
            "alpha.py": f'import beta\nMARKER = "{tag}"\n',
            "beta.py": f'MARKER = "{tag}"\n',
        },
    ).resolve()
    files = [repo / name for name in ("main.py", "alpha.py", "beta.py")]
    opened: list[Path] = []
    _watch_opens(monkeypatch, files, opened.append)

    part = _part(repo)

    assert part.established, part.reason
    assert sorted(opened) == sorted(files), "each file must be opened once"


def test_code_identity_uses_no_private_name_of_code_hash() -> None:
    """The code_identity module imports no private name from code_hash and reads no private attribute of the code_hash module."""
    assert code_identity_module.__file__ is not None
    tree = ast.parse(Path(code_identity_module.__file__).read_text(encoding="utf-8"))
    module_names: set[str] = set()
    private: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("code_hash"):
            private += [alias.name for alias in node.names if alias.name.startswith("_")]
        elif isinstance(node, ast.ImportFrom):
            module_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "code_hash"
            )
        elif isinstance(node, ast.Import):
            module_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name.endswith(".code_hash")
            )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and ast.unparse(node.value) in module_names
        ):
            private.append(node.attr)

    assert private == []


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


def test_the_node_state_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A node whose starting file has been deleted, and a node whose code_part raises RuntimeError, return a non-empty state without raising."""
    repo = _write(
        tmp_path / "repo", {"deleted.py": "VALUE = 1\n", "main.py": "VALUE = 2\n"}
    ).resolve()
    deleted = repo / "deleted.py"
    deleted_node = CodeIdentityNode(name="deleted::code", start=deleted, repository_root=repo)
    failing_node = CodeIdentityNode(
        name="failing::code", start=repo / "main.py", repository_root=repo
    )
    deleted.unlink()

    def failing_code_part(*args: Any, **kwargs: Any) -> CodePart:
        """Raise as an unexpected failure of the analysis would."""
        raise RuntimeError("the analysis failed")

    deleted_state = deleted_node.state()
    monkeypatch.setattr(code_identity_module, "code_part", failing_code_part)
    failing_state = failing_node.state()

    for state in (deleted_state, failing_state):
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
    """Calling a node's state three times with no edit parses each listed Python file at least once and at most twice (once for its code hash and once for its import scan), all in the first call, and after an edit to one listed file the next call parses only the edited file again, at most twice."""
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
    assert all(parsed_first.count(label) <= 2 for label in labels), parsed_first
    assert parsed_again == []
    assert edited != first
    assert set(parsed_after_edit) == {"beta"}, parsed_after_edit
    assert len(parsed_after_edit) <= 2, parsed_after_edit


def test_an_old_unchanged_file_is_not_read_again_and_a_change_of_size_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every listed file counted as old enough that its stat can be trusted, a second call of a node's state opens no listed file and gives the same state, and an edit that changes a file's size but keeps its modification time changes the next state."""
    tag = uuid.uuid4().hex
    repo = _write(
        tmp_path / "repo",
        {
            "ci_stat_main.py": f'import ci_stat_alpha\nMARKER = "main-{tag}"\n',
            "ci_stat_alpha.py": f'import ci_stat_beta\nMARKER = "alpha-{tag}"\n',
            "ci_stat_beta.py": f'MARKER = "beta-{tag}"\nVALUE = 1\n',
        },
    ).resolve()
    files = sorted(repo.glob("*.py"))
    # The cache trusts a stat only when its modification and change times are older than
    # the window; os.utime cannot set the change time, so the window is moved instead.
    monkeypatch.setattr(code_identity_module, "_RACY_NS", -10_000_000_000)
    monkeypatch.syspath_prepend(str(repo))
    node = CodeIdentityNode(
        name="step::code", start=repo / "ci_stat_main.py", repository_root=repo
    )
    opened: list[Path] = []
    _watch_opens(monkeypatch, files, opened.append)

    first = node.state()
    opened_first = set(opened)
    opened.clear()
    second = node.state()
    opened_again = list(opened)
    beta = repo / "ci_stat_beta.py"
    status = beta.stat()
    beta.write_text(f'MARKER = "beta-{tag}"\nVALUE = 1000\n')
    os.utime(beta, ns=(status.st_atime_ns, status.st_mtime_ns))
    edited = node.state()

    assert opened_first == set(files), (
        f"opened {opened_first}; none means the analysis does not open files with open, "
        "io.open or os.open"
    )
    assert opened_again == []
    assert second == first
    assert edited != first


def test_the_site_scan_is_not_repeated_for_the_same_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a node's first state, the state of a node of another starting file, with the same search path and every file old enough that its stat can be trusted, opens none of the .pth files or the editable finder module again."""
    install = _editable_install(tmp_path, "finder_mapping")
    site = Path(install.search_path[0])
    compat = _write(tmp_path / "compat" / "src", {"ci_scan_compat.py": "VALUE = 1\n"})
    (site / "ci_scan_compat.pth").write_text(f"{compat}\n")
    scanned = [
        site / "__editable__.edpkg-0.1.pth",
        site / f"{_FINDER}.py",
        site / "ci_scan_compat.pth",
    ]
    imports = f"import {install.package}.core\nimport ci_scan_compat\n"
    repo = _write(
        tmp_path / "repo",
        {
            "ci_scan_one.py": f"{imports}VALUE = 1\n",
            "ci_scan_two.py": f"{imports}VALUE = 2\n",
        },
    ).resolve()
    # Every stat counts as old enough to be trusted, so the cache can check a file that it
    # read before without opening it.
    monkeypatch.setattr(code_identity_module, "_RACY_NS", -10_000_000_000)
    monkeypatch.setattr(
        sys, "path", [str(repo), *install.search_path, str(compat), *sys.path]
    )
    opened: list[Path] = []
    _watch_opens(monkeypatch, scanned, opened.append)

    first = CodeIdentityNode(
        name="one::code", start=repo / "ci_scan_one.py", repository_root=repo
    ).state()
    opened_first = set(opened)
    opened.clear()
    second = CodeIdentityNode(
        name="two::code", start=repo / "ci_scan_two.py", repository_root=repo
    ).state()
    opened_again = list(opened)

    assert first and second
    assert opened_first == {path.resolve() for path in scanned}, (
        f"opened {opened_first} in the first state; none means the scan does not open files "
        "with open, io.open or os.open"
    )
    assert opened_again == []
