"""The code part of a step's identity: the first-party files that the step imports.

A step's code part is the set of first-party files that its starting point imports,
directly or through other files, each with a hash of its code. The import analysis
reads the files and imports nothing. It follows every ``import`` statement at any depth,
including imports inside functions and class bodies, but not imports under
``if TYPE_CHECKING:`` and not imports inside the ``if __name__ == "__main__":`` block of
a file other than the starting point.

Each imported name is resolved as the interpreter resolves it. The directories of the
search path are tried first, in order, and an editable finder's ``MAPPING`` (see below)
is used only for a name that the search path does not find, because setuptools adds its
finder after the path finder. A package whose ``__init__.py`` assigns ``__path__`` from
``pkgutil.extend_path(__path__, __name__)``, spelled as ``pkgutil.extend_path`` after
``import pkgutil``, as ``extend_path`` after ``from pkgutil import extend_path`` (either
with any alias), or as ``__import__("pkgutil").extend_path``, has its submodules
resolved in its own directory and then in each ``<entry>/<package name>`` directory that
the path finder finds a package portion in, for each entry of the search path in order,
as ``extend_path`` makes its ``__path__``. The ``vivarium`` package of this repository
is such a package, so ``vivarium.<lib>`` of an editable library resolves to that
library's source. A module that resolves to a compiled extension or to a ``.pyc`` file
without source is listed by the SHA-256 of its bytes, so rebuilding it changes the code
part.

First-party code is code in the pipeline's declared repository root, or in the source of
an editable install. An editable install is recognised in either of the two forms that
setuptools writes into a ``site-packages`` directory on the search path: a ``.pth`` file
that adds a source directory to the search path (compat mode), or an editable finder
module, ``__editable___*_finder.py``, whose literal ``MAPPING`` maps each package or
module name to its source, which an import of that name then resolves to. A ``MAPPING``
target that is a directory without ``__init__.py`` is a namespace package at that
directory. Neither form is imported or run. A file in an editable install's source is
named relative to the root of that source. For a ``.pth`` file the root is the directory
that it adds. For a ``MAPPING`` name whose dotted parts are the last components of its
target's resolved path, such as ``vivarium.dagger`` for ``/src/vivarium/dagger``, the root
is that path with those components removed (``/src``), so the two forms give one file the
same name; for any other ``MAPPING`` name the root is the directory that holds its target.
Code that the environment installs, including
anything under a ``site-packages`` or ``dist-packages`` directory or a virtual
environment inside the repository, is not first-party; it belongs to the environment
part of the identity, and the analysis stops there. The standard library and built-in
modules are installed code.

A module can add data files to the code part. The code that runs when a module is
imported is its top level, including the bodies of its classes but not the bodies of its
functions and lambdas. A call there adds a data file for each argument, positional or by
keyword, whose path the analysis computes, and a call of ``open()``, ``read_text()`` or
``read_bytes()`` adds the path that it is called on; the path must be built from the
module's own location, ``__file__``, and name an existing first-party file. So, with
``HERE = Path(__file__).parent``, each of ``open(HERE / "t.txt")``,
``pd.read_csv(HERE / "t.csv")``, ``np.load(HERE / "a.npy")``,
``yaml.safe_load(HERE / "c.yaml")``, ``json.load(open(HERE / "c.json"))`` and
``(HERE / "c.txt").read_text()`` adds its file. A path is computed from string literals,
``__file__``, the ``/`` operator, ``.parent``, ``.parents[<n>]``, ``Path()``,
``PurePath()`` (also as ``pathlib.Path()`` and ``pathlib.PurePath()``),
``os.path.join()``, ``.joinpath()``, ``os.path.dirname()``, ``str()``, ``os.fspath()``,
``os.path.abspath()``, ``os.path.realpath()``, ``os.path.normpath()``, ``.resolve()``,
``.absolute()``, and module-level names. Only bindings that run in module scope count:
those at the module's top level or nested, at any depth, in the blocks of its ``if``,
``elif``, ``else``, ``for``, ``while``, ``with``, ``try``, ``except``, ``finally`` and
``match`` statements, together with each name that a function or class declares
``global``. A parameter, and a binding inside a function, a lambda or a comprehension, do
not count. A name is used only when it has exactly one such binding and that binding is
a plain assignment ``NAME = ...`` or ``NAME: <type> = ...`` at the module's top level,
also as one target of ``A = B = ...``. A name with a second binding, or whose one binding
is of another kind, such as an augmented assignment, a ``for`` or ``with`` target,
``:=``, unpacking, an import, a ``def`` or ``class``, ``del``, or an assignment nested in
one of those blocks, is unknown, and so is ``__file__`` when the module binds it; a path
that uses an unknown name adds nothing.

A Python file is hashed with :func:`~vivarium.dagger.backend.code_hash.python_code_hash`,
and a data file by its bytes. Each Python file content is read once per check of a node's
state, and parsed at most twice: once for its hash and once for its imports, each result
kept for the process. Each file is named by its path relative to the root that holds it,
so the same content in two checkouts at different paths gives the same code part.

The code part is not established when the starting point is installed code or is outside
every first-party root, or when an import resolves to a file that is neither first-party
nor installed. A node whose state is not established makes its step run on every build.

"""

import ast
import hashlib
import json
import os
import sys
import sysconfig
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.machinery import (
    BYTECODE_SUFFIXES,
    EXTENSION_SUFFIXES,
    SOURCE_SUFFIXES,
    all_suffixes,
)
from pathlib import Path, PurePath
from typing import Any

from vivarium.dagger.backend.code_hash import parse_source, python_code_hash


@dataclass(frozen=True)
class CodePart:
    """The first-party files of a step's code part, or the reason it is not established."""

    files: Mapping[str, str]
    """Each first-party file's name, relative to the root that holds it, mapped to the
    digest of its code (a Python file) or of its bytes (a data file, a compiled extension
    or a ``.pyc`` file without source). Empty when the code part is not established."""
    reason: str | None = None
    """Why the code part is not established, naming the file that caused it, or ``None``
    when it is established."""

    @property
    def established(self) -> bool:
        """Whether the code part could be computed."""
        return self.reason is None


def code_part(
    start: Path | str,
    repository_root: Path | str | None,
    search_path: Sequence[str],
) -> CodePart:
    """Return the code part of a step whose starting point is the Python file ``start``.

    Parameters
    ----------
    start
        The Python file the step's code starts in, such as the file of the module that
        defines a step's function. The starting file's own ``__main__`` block is followed.
    repository_root
        The pipeline's declared repository root, or ``None`` when the pipeline declares
        none; then only the directories of editable installs are first-party.
    search_path
        The directories that imports are resolved against, in order, as in
        :data:`sys.path`. Editable installs are recognised from the ``site-packages``
        directories on it, and entries that are not directories, such as an editable
        finder's path hook, are ignored.

    Returns
    -------
        The code part. It is not established when ``start`` is installed code or is
        outside every first-party root, or when an import resolves to a file outside the
        first-party roots that is not installed code; the reason names the file. A
        first-party file that cannot be parsed is listed with its bytes hash, and the
        analysis follows no imports from it.
    """
    start_path = os.path.abspath(start)
    root = None if repository_root is None else os.path.realpath(repository_root)
    directories = tuple(os.path.abspath(entry) for entry in search_path)
    key = (start_path, root, directories, sys.get_int_max_str_digits())
    memo: _Memo = {}
    cached = _analyses.get(key)
    if cached is not None and _unchanged(cached.listed, memo):
        return cached.part
    walk = _Walk(root, directories, memo)
    part = walk.run(start_path)
    if part.established:
        _analyses[key] = _Analysis(part, tuple(walk.listed))
    else:
        _analyses.pop(key, None)
    return part


@dataclass
class CodeIdentityNode:
    """A step's identity node, whose state is a hash of the step's code part and target.

    The node implements pytask's node protocol. Its state is computed from
    :func:`code_part` for the file ``start``, with the pipeline's ``repository_root`` and
    the build process's :data:`sys.path`, together with ``target``, so a step that
    switches to another function of an unchanged module runs again. When the code part
    is not established, the state is a new string on every call, so the step runs on
    every build. The state is never ``None`` or empty, and :meth:`state` never raises.
    Within one process the code part of one starting point is analysed once for each
    content of the files it lists.

    Two nodes with the same ``name``, ``start``, ``repository_root`` and ``target`` are
    equal.
    """

    name: str
    """Identifier of the node, unique within its task, such as ``"<step>::code"``."""
    start: Path
    """The resolved Python file the step's code starts in."""
    repository_root: Path | None = None
    """The pipeline's resolved repository root, or ``None`` when it declares none."""
    target: str | None = None
    """The import reference of the function the step calls, ``"module:qualname"``, or
    ``None`` for a node built without one."""
    attributes: dict[Any, Any] = field(default_factory=dict, compare=False)
    """pytask node attributes; unused, kept to satisfy the node protocol."""

    @property
    def signature(self) -> str:
        """Return a hash of the node's name, which is stable across processes."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def state(self) -> str:
        """Return a hash of the code part and target, or a new string if not established."""
        # A failed analysis makes the step run, rather than failing the build.
        try:
            part = code_part(self.start, self.repository_root, list(sys.path))
        except Exception:
            return _not_established()
        return _state(part, self.target) if part.established else _not_established()

    def load(self, is_product: bool = False) -> "CodeIdentityNode":
        """Return the node itself; a step's function does not receive it."""
        return self

    def save(self, value: Any) -> None:
        """Refuse to save, since the node is read-only.

        Raises
        ------
        NotImplementedError
            Always.
        """
        raise NotImplementedError("A CodeIdentityNode is a dependency and cannot be saved.")


_INSTALLED_NAMES = frozenset({"site-packages", "dist-packages"})
"""Names of the directories that hold installed code."""

_EDITABLE = "<editable>/"
"""The prefix of the name of a file in an editable install's source outside the repository."""

_OUTSIDE = "outside the repository root and the source of every editable install"

_BYTES = "bytes:"
"""The prefix of the digest of a file hashed by its bytes."""

_STATE_VERSION = "code-part-2"
"""The version of the canonical text that a node's state hashes."""

_RACY_NS = 2_000_000_000
"""How much older than its reading a file's last change must be for its stat to be trusted."""

_SOURCE = tuple(SOURCE_SUFFIXES)

_MODULE_SUFFIXES = (*EXTENSION_SUFFIXES, *SOURCE_SUFFIXES, *BYTECODE_SUFFIXES)
"""The suffixes of module files, in the order the path finder tries them."""

_PATH_READS = frozenset({"open", "read_text", "read_bytes"})
"""Path methods that open or read the file of the path they are called on."""

_PATH_TYPES = frozenset({"Path", "pathlib.Path", "PurePath", "pathlib.PurePath"})

_SAME_PATH_CALLS = frozenset(
    {"str", "os.fspath", "os.path.abspath", "os.path.realpath", "os.path.normpath"}
)
"""Calls that return the path their one argument names."""

_IMPORT_CALLS = frozenset({"__import__", "importlib.import_module"})

_MAX_DEPTH = 32
"""The deepest path expression that is computed."""

_Memo = dict[str, tuple[tuple[int, ...], str, bytes]]
"""The stat, SHA-256 and bytes of each Python file read during one call of code_part."""


@dataclass(frozen=True)
class _Import:
    """An import statement's module, imported names, relative level and block."""

    module: str
    names: tuple[str, ...]
    level: int
    main_only: bool


@dataclass(frozen=True)
class _Read:
    """An expression that may name a file that a module reads at import."""

    path: ast.expr
    main_only: bool


@dataclass(frozen=True)
class _Scan:
    """What the analysis follows in a file, and how its paths are computed."""

    imports: tuple[_Import, ...]
    reads: tuple[_Read, ...]
    assignments: Mapping[str, ast.expr]
    """The value of each name that the module binds once, by a top-level assignment."""
    file_known: bool
    """Whether ``__file__`` is not bound in the module."""
    extends_path: bool
    """Whether the module assigns ``__path__`` from ``pkgutil.extend_path``."""


@dataclass(frozen=True)
class _Content:
    """The code digest of one Python file content, and its scan if it parses."""

    digest: str
    scan: _Scan | None


@dataclass(frozen=True)
class _Seen:
    """A file's stat and SHA-256 when it was last read, and whether the stat is trusted."""

    stat: tuple[int, ...]
    sha: str
    trusted: bool


@dataclass(frozen=True)
class _Analysis:
    """An established code part and the path and SHA-256 of each file it lists."""

    part: CodePart
    listed: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Module:
    """A module's file, if it has one, and its search path if it is a package."""

    file: str | None
    path: tuple[str, ...] | None


@dataclass(frozen=True)
class _Resolved:
    """The module files that an import loads, and the last package's search path."""

    files: tuple[str, ...]
    path: tuple[str, ...] | None


@dataclass(frozen=True)
class _PathValue:
    """A path computed from a syntax tree, and whether it is built from ``__file__``."""

    path: str
    from_file: bool


_seen: dict[str, _Seen] = {}
"""The last reading of each file, by path."""

_contents: dict[tuple[str, int], _Content] = {}
"""Each Python file content, by SHA-256 of its bytes and the int-string digit limit."""

_plain_inits: set[str] = set()
"""The SHA-256 of each ``__init__.py`` content that does not mention ``extend_path``."""

_analyses: dict[tuple[str, str | None, tuple[str, ...], int], _Analysis] = {}
"""The last established code part of each starting file, root, search path and limit."""


class _Walk:
    """One analysis of the first-party files that a starting file imports."""

    def __init__(self, root: str | None, search_path: Sequence[str], memo: _Memo) -> None:
        self.root = root
        self.memo = memo
        self.listings: dict[str, frozenset[str]] = {}
        self.mapping: dict[str, str] = {}
        self.editable_roots: list[str] = []
        self.environments: dict[str, bool] = {}
        self.directories = self._search_directories(search_path)
        self.search_set = {os.path.realpath(directory) for directory in self.directories}
        self.visited: set[str] = set()
        self.queue: list[tuple[str, str, bool]] = []
        self.python: dict[str, str] = {}
        self.data: dict[str, str] = {}
        self.listed: list[tuple[str, str]] = []
        self.reason: str | None = None

    def run(self, start: str) -> CodePart:
        """Return the code part of the starting file ``start``."""
        real = os.path.realpath(start)
        if not os.path.isfile(real):
            return CodePart({}, f"The starting file {start} does not exist.")
        kind, name = self._classify(real)
        if kind == "installed":
            return CodePart({}, f"The starting file {start} is installed code.")
        if kind != "first":
            return CodePart({}, f"The starting file {start} is {_OUTSIDE}.")
        self.visited.add(real)
        self.queue.append((start, name, True))
        self._add_packages(real)
        while self.queue and self.reason is None:
            self._visit(*self.queue.pop())
        if self.reason is not None:
            return CodePart({}, self.reason)
        files = {**self.data, **self.python}
        return CodePart(dict(sorted(files.items())))

    def _search_directories(self, search_path: Sequence[str]) -> list[str]:
        """Return the directories on the search path and those that ``.pth`` files add."""
        given = set(search_path)
        directories: list[str] = []
        for entry in search_path:
            if entry in directories or not os.path.isdir(entry):
                continue
            directories.append(entry)
            if os.path.basename(entry) in _INSTALLED_NAMES:
                for added in self._read_site(entry):
                    if added not in given and added not in directories:
                        directories.append(added)
        return directories

    def _read_site(self, site: str) -> list[str]:
        """Return the directories that ``site``'s ``.pth`` files add; read its finders."""
        added: list[str] = []
        for name in sorted(self._listing(site)):
            path = os.path.join(site, name)
            if name.startswith("."):
                continue
            if name.endswith(".pth"):
                for directory in _pth_directories(path, site):
                    added.append(directory)
                    self.editable_roots.append(os.path.realpath(directory))
            elif name.startswith("__editable___") and name.endswith("_finder.py"):
                for package, target in _finder_mapping(path).items():
                    self.mapping.setdefault(package, target)
                    self.editable_roots.append(_editable_root(package, target))
        return added

    def _listing(self, directory: str) -> frozenset[str]:
        """Return the names in a directory, or none if it cannot be listed."""
        listing = self.listings.get(directory)
        if listing is None:
            try:
                listing = frozenset(os.listdir(directory))
            except OSError:
                listing = frozenset()
            self.listings[directory] = listing
        return listing

    def _classify(self, real: str) -> tuple[str, str]:
        """Return a resolved file's kind (installed, first-party or foreign) and its name."""
        if _installed(real):
            return "installed", ""
        if self.root is not None and _within(real, self.root):
            holder, prefix = self.root, ""
        else:
            holders = [root for root in self.editable_roots if _within(real, root)]
            if not holders:
                return "foreign", ""
            holder, prefix = max(holders, key=len), _EDITABLE
        if self._in_environment(real, holder):
            return "installed", ""
        return "first", prefix + PurePath(os.path.relpath(real, holder)).as_posix()

    def _in_environment(self, real: str, holder: str) -> bool:
        """Return whether a file is in a virtual environment below the root ``holder``."""
        directory = os.path.dirname(real)
        while directory != holder and _within(directory, holder):
            found = self.environments.get(directory)
            if found is None:
                found = os.path.isfile(os.path.join(directory, "pyvenv.cfg"))
                self.environments[directory] = found
            if found:
                return True
            directory = os.path.dirname(directory)
        return False

    def _add_packages(self, start: str) -> None:
        """Queue the ``__init__.py`` of each package that holds the starting file."""
        directory = os.path.dirname(start)
        if os.path.basename(start) == "__init__.py":
            directory = os.path.dirname(directory)
        while directory not in self.search_set and "__init__.py" in self._listing(directory):
            init = os.path.realpath(os.path.join(directory, "__init__.py"))
            kind, name = self._classify(init)
            if kind != "first":
                return
            if init not in self.visited:
                self.visited.add(init)
                self.queue.append((init, name, False))
            parent = os.path.dirname(directory)
            if parent == directory:
                return
            directory = parent

    def _visit(self, file: str, name: str, is_start: bool) -> None:
        """List one first-party Python file and follow what it imports and opens."""
        try:
            sha, data = _file_sha(file, self.memo)
            content = _python_content(file, sha, data)
        except OSError:
            role = "starting" if is_start else "first-party"
            self.reason = f"The {role} file {file} cannot be read."
            return
        self.python[name] = content.digest
        self.listed.append((file, sha))
        scan = content.scan
        if scan is None:
            return
        for statement in scan.imports:
            if is_start or not statement.main_only:
                self._follow(statement, file)
                if self.reason is not None:
                    return
        evaluator = _PathEvaluator(file if scan.file_known else None, scan.assignments)
        for read in scan.reads:
            if is_start or not read.main_only:
                self._add_data(evaluator.value(read.path))

    def _follow(self, statement: _Import, file: str) -> None:
        """Queue the first-party files that an import statement in ``file`` loads."""
        parts = statement.module.split(".") if statement.module else []
        module: _Resolved | None
        package: str | None
        if statement.level:
            base = os.path.dirname(file)
            for _ in range(statement.level - 1):
                base = os.path.dirname(base)
            module = self._resolve(parts, (base,), None) if parts else _Resolved((), (base,))
            package = None
        else:
            if not parts or parts[0] in sys.builtin_module_names:
                return
            module = self._resolve(parts, self.directories, "")
            package = statement.module
        if module is None:
            return
        files = list(module.files)
        if module.path is not None:
            for name in statement.names:
                if name != "*":
                    submodule = self._resolve([name], module.path, package)
                    if submodule is not None:
                        files += submodule.files
        for found in files:
            self._add(found, file)
            if self.reason is not None:
                return

    def _resolve(
        self, parts: Sequence[str], path: Sequence[str], package: str | None
    ) -> _Resolved | None:
        """Return the module files that importing ``parts`` from ``path`` loads, or None."""
        files: list[str] = []
        for index, part in enumerate(parts):
            name = None if package is None else f"{package}.{part}" if package else part
            module = self._find(part, path, name)
            if module is None:
                if not self._maps_below(name):
                    return _Resolved(tuple(files), None) if index else None
                path, package = (), name
                continue
            if module.file is not None:
                files.append(module.file)
            if module.path is None:
                return _Resolved(tuple(files), None)
            path, package = module.path, name
        return _Resolved(tuple(files), tuple(path))

    def _maps_below(self, name: str | None) -> bool:
        """Return whether an editable finder maps a module inside the package ``name``."""
        return name is not None and any(key.startswith(f"{name}.") for key in self.mapping)

    def _find(self, part: str, path: Sequence[str], name: str | None) -> _Module | None:
        """Return the module ``part`` as the import system finds it on ``path``, or None."""
        portions: list[str] = []
        for directory in path:
            candidate = os.path.join(directory, part)
            is_entry = part in self._listing(directory)
            if is_entry:
                init = self._module_file(candidate, "__init__")
                if init is not None:
                    return self._package(init, candidate, part, path)
            module = self._module_file(directory, part)
            if module is not None:
                return _Module(module, None)
            if is_entry and os.path.isdir(candidate):
                portions.append(candidate)
        if portions:
            return _Module(None, tuple(portions))
        # setuptools appends its editable finder to sys.meta_path, after the path finder.
        if name is not None and name in self.mapping:
            return self._mapped(self.mapping[name], part, path)
        return None

    def _package(self, init: str, directory: str, part: str, path: Sequence[str]) -> _Module:
        """Return a regular package with the search path that its ``__init__`` gives it."""
        portions = [directory]
        if init.endswith(_SOURCE) and self._extends_path(init):
            for entry in path:
                portion = self._portion(entry, part)
                if portion is not None and portion not in portions:
                    portions.append(portion)
        return _Module(init, tuple(portions))

    def _portion(self, entry: str, part: str) -> str | None:
        """Return ``<entry>/<part>`` if the path finder finds a package portion there."""
        candidate = os.path.join(entry, part)
        if part not in self._listing(entry) or not os.path.isdir(candidate):
            return None
        if self._module_file(candidate, "__init__") is not None:
            return candidate
        return candidate if self._module_file(entry, part) is None else None

    def _extends_path(self, init: str) -> bool:
        """Return whether a package's ``__init__.py`` extends its path with pkgutil."""
        try:
            sha, data = _file_sha(init, self.memo)
        except OSError:
            return False
        content = _contents.get((sha, sys.get_int_max_str_digits()))
        if content is None:
            if sha in _plain_inits:
                return False
            try:
                data = Path(init).read_bytes() if data is None else data
            except OSError:
                return False
            if b"extend_path" not in data:
                _plain_inits.add(sha)
                return False
            content = _python_content(init, sha, data)
        return content.scan is not None and content.scan.extends_path

    def _mapped(self, target: str, part: str, path: Sequence[str]) -> _Module | None:
        """Return the module that an editable finder maps a name to, or None."""
        init = os.path.join(target, "__init__.py")
        if os.path.isfile(init):
            return self._package(init, target, part, path)
        try:
            candidates = [
                str(PurePath(target).with_suffix(suffix)) for suffix in all_suffixes()
            ]
        except ValueError:
            candidates = []
        for candidate in candidates:
            if os.path.isfile(candidate):
                return _Module(candidate, None)
        return _Module(None, (target,)) if os.path.isdir(target) else None

    def _module_file(self, directory: str, stem: str) -> str | None:
        """Return the module file ``stem`` in ``directory``, as the path finder picks it."""
        listing = self._listing(directory)
        for suffix in _MODULE_SUFFIXES:
            path = os.path.join(directory, stem + suffix)
            if stem + suffix in listing and os.path.isfile(path):
                return path
        return None

    def _add(self, found: str, importer: str) -> None:
        """Queue or list a file that an import loads, or end the analysis if foreign."""
        real = os.path.realpath(found)
        if real in self.visited:
            return
        self.visited.add(real)
        kind, name = self._classify(real)
        if kind == "foreign":
            self.reason = (
                f"{importer} imports {found}, which is {_OUTSIDE} and is not installed code."
            )
        elif kind == "first" and found.endswith(_SOURCE):
            self.queue.append((found, name, False))
        elif kind == "first":
            try:
                sha, _ = _file_sha(found, None)
            except OSError:
                self.reason = f"The first-party file {found} cannot be read."
                return
            self.python[name] = _BYTES + sha
            self.listed.append((found, sha))

    def _add_data(self, value: _PathValue | None) -> None:
        """List a first-party data file whose path a module builds from its ``__file__``."""
        if value is None or not value.from_file or not os.path.isabs(value.path):
            return
        if not os.path.isfile(value.path):
            return
        real = os.path.realpath(value.path)
        if real in self.visited:
            return
        kind, name = self._classify(real)
        if kind != "first" or name in self.data:
            return
        try:
            sha, _ = _file_sha(value.path, None)
        except OSError:
            return
        self.data[name] = _BYTES + sha
        self.listed.append((value.path, sha))


@dataclass(frozen=True)
class _PathEvaluator:
    """Compute the paths that a module builds from its ``__file__`` and string literals."""

    file: str | None
    assignments: Mapping[str, ast.expr]

    def value(self, expression: ast.expr | None, depth: int = 0) -> _PathValue | None:
        """Return the path that ``expression`` computes, or None."""
        if expression is None or depth > _MAX_DEPTH:
            return None
        if isinstance(expression, ast.Constant):
            text = expression.value
            return _PathValue(text, False) if isinstance(text, str) else None
        if isinstance(expression, ast.Name):
            if expression.id == "__file__":
                return None if self.file is None else _PathValue(self.file, True)
            return self.value(self.assignments.get(expression.id), depth + 1)
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Div):
            left = self.value(expression.left, depth + 1)
            return _joined([left, self.value(expression.right, depth + 1)])
        if isinstance(expression, ast.Attribute) and expression.attr == "parent":
            return _parent(self.value(expression.value, depth + 1), 1)
        if isinstance(expression, ast.Subscript):
            return self._parents(expression, depth)
        if isinstance(expression, ast.Call):
            return self._call(expression, depth)
        return None

    def _parents(self, expression: ast.Subscript, depth: int) -> _PathValue | None:
        """Return the path that ``<path>.parents[<int>]`` computes."""
        owner, index = expression.value, expression.slice
        if not isinstance(owner, ast.Attribute) or owner.attr != "parents":
            return None
        if not isinstance(index, ast.Constant) or type(index.value) is not int:
            return None
        if index.value < 0:
            return None
        return _parent(self.value(owner.value, depth + 1), index.value + 1)

    def _call(self, call: ast.Call, depth: int) -> _PathValue | None:
        """Return the path that a call computes, for the calls that build paths."""
        name = _dotted(call.func)
        arguments = [self.value(argument, depth + 1) for argument in call.args]
        if name in _PATH_TYPES or name == "os.path.join":
            return _joined(arguments)
        if len(arguments) == 1 and name == "os.path.dirname":
            return _parent(arguments[0], 1)
        if len(arguments) == 1 and name in _SAME_PATH_CALLS:
            return arguments[0]
        if not isinstance(call.func, ast.Attribute):
            return None
        receiver = self.value(call.func.value, depth + 1)
        if call.func.attr in ("resolve", "absolute") and not arguments:
            return receiver
        if call.func.attr == "joinpath":
            return _joined([receiver, *arguments])
        return None


def _joined(values: Sequence[_PathValue | None]) -> _PathValue | None:
    """Return paths joined as ``os.path.join`` joins them, or None if one is unknown."""
    known = [value for value in values if value is not None]
    if not known or len(known) != len(values):
        return None
    start = max((i for i, value in enumerate(known) if os.path.isabs(value.path)), default=0)
    kept = known[start:]
    path = os.path.normpath(os.path.join(*(value.path for value in kept)))
    return _PathValue(path, any(value.from_file for value in kept))


def _parent(value: _PathValue | None, levels: int) -> _PathValue | None:
    """Return the directory ``levels`` levels above a path."""
    if value is None:
        return None
    path = value.path
    for _ in range(levels):
        path = os.path.dirname(path)
    return _PathValue(path, value.from_file)


def _dotted(expression: ast.expr) -> str | None:
    """Return the dotted name that an expression of names and attributes spells, or None."""
    parts: list[str] = []
    while isinstance(expression, ast.Attribute):
        parts.append(expression.attr)
        expression = expression.value
    if not isinstance(expression, ast.Name):
        return None
    parts.append(expression.id)
    return ".".join(reversed(parts))


def _scan(tree: ast.Module) -> _Scan:
    """Return what the analysis follows in a module's syntax tree."""
    imports: list[_Import] = []
    reads: list[_Read] = []
    path_values: list[ast.expr] = []
    pkgutil_names: set[str] = set()
    extend_names: set[str] = set()
    # A stack, not recursion, so that deeply nested code does not exceed the recursion limit.
    stack: list[tuple[ast.AST, bool, bool]] = [(tree, False, False)]
    while stack:
        node, deferred, main_only = stack.pop()
        children: list[tuple[ast.AST, bool, bool]]
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            children = [(child, deferred, main_only) for child in node.orelse]
        elif isinstance(node, ast.If) and _is_main_guard(node.test):
            children = [(child, deferred, True) for child in node.body]
            children += [(child, deferred, main_only) for child in node.orelse]
        else:
            if isinstance(node, ast.Import):
                imports += [_Import(alias.name, (), 0, main_only) for alias in node.names]
                pkgutil_names.update(_aliases(node, "pkgutil"))
            elif isinstance(node, ast.ImportFrom):
                names = tuple(alias.name for alias in node.names)
                imports.append(_Import(node.module or "", names, node.level or 0, main_only))
                if node.module == "pkgutil" and not node.level:
                    extend_names.update(_aliases(node, "extend_path"))
            elif isinstance(node, ast.Call) and not deferred:
                reads += [_Read(path, main_only) for path in _read_paths(node)]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not deferred:
                if node.value is not None and "__path__" in _target_names(node):
                    path_values.append(node.value)
            body = _deferred_ids(node)
            children = [
                (child, deferred or id(child) in body, main_only)
                for child in ast.iter_child_nodes(node)
            ]
        stack.extend(reversed(children))
    extends = any(
        _is_extend_path(value, pkgutil_names, extend_names) for value in path_values
    )
    bindings = _module_bindings(tree)
    return _Scan(
        tuple(imports),
        tuple(reads),
        _assignments(tree, bindings),
        "__file__" not in bindings,
        extends,
    )


def _deferred_ids(node: ast.AST) -> set[int]:
    """Return the ids of the children of ``node`` that do not run when it runs."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {id(statement) for statement in node.body}
    if isinstance(node, ast.Lambda):
        return {id(node.body)}
    return set()


def _is_type_checking(test: ast.expr) -> bool:
    """Return whether an ``if`` test is ``TYPE_CHECKING`` or ``<module>.TYPE_CHECKING``."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_main_guard(test: ast.expr) -> bool:
    """Return whether an ``if`` test is ``__name__ == "__main__"``."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    names = [side.id for side in sides if isinstance(side, ast.Name)]
    constants = [side.value for side in sides if isinstance(side, ast.Constant)]
    return names == ["__name__"] and constants == ["__main__"]


def _read_paths(call: ast.Call) -> list[ast.expr]:
    """Return a call's arguments, and the path it reads if it is a path read."""
    paths = [argument for argument in call.args if not isinstance(argument, ast.Starred)]
    paths += [keyword.value for keyword in call.keywords if keyword.arg is not None]
    if isinstance(call.func, ast.Attribute) and call.func.attr in _PATH_READS:
        paths.append(call.func.value)
    return paths


def _module_bindings(tree: ast.Module) -> Counter[str]:
    """Return how often each name is bound in module scope or declared global."""
    bindings: Counter[str] = Counter()
    # A stack, not recursion, so that deeply nested code does not exceed the recursion limit.
    stack: list[tuple[ast.AST, bool]] = [(tree, True)]
    while stack:
        node, module_scope = stack.pop()
        if not module_scope:
            if isinstance(node, ast.Global):
                bindings.update(node.names)
            stack.extend((child, False) for child in ast.iter_child_nodes(node))
            continue
        bindings.update(_bound_names(node))
        stack.extend(_module_scope_children(node))
    return bindings


def _module_scope_children(node: ast.AST) -> list[tuple[ast.AST, bool]]:
    """Return a module-scope node's children, each with whether it runs in module scope."""
    if isinstance(node, ast.comprehension):
        # The target is local to the comprehension, but a := in it binds in module scope.
        return [(node.iter, True), *((condition, True) for condition in node.ifs)]
    if isinstance(node, ast.AnnAssign) and node.value is None:
        # An annotation without a value binds nothing.
        return [(node.annotation, True)]
    local: set[int] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        local = {id(statement) for statement in node.body}
    elif isinstance(node, ast.Lambda):
        local = {id(node.body)}
    return [(child, id(child) not in local) for child in ast.iter_child_nodes(node)]


def _bound_names(node: ast.AST) -> list[str]:
    """Return the names that a syntax-tree node binds in the scope it runs in."""
    if isinstance(node, ast.Name):
        return [node.id] if isinstance(node.ctx, (ast.Store, ast.Del)) else []
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [
            alias.asname or alias.name.split(".")[0]
            for alias in node.names
            if alias.name != "*"
        ]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return [] if node.name is None else [node.name]
    if isinstance(node, ast.MatchMapping):
        return [] if node.rest is None else [node.rest]
    return []


def _aliases(node: ast.Import | ast.ImportFrom, imported: str) -> list[str]:
    """Return the names that an import statement binds to the name ``imported``."""
    return [alias.asname or alias.name for alias in node.names if alias.name == imported]


def _target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Return the plain names that an assignment assigns to."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _is_extend_path(value: ast.expr, modules: set[str], functions: set[str]) -> bool:
    """Return whether an expression is pkgutil's ``extend_path(__path__, __name__)``."""
    if not isinstance(value, ast.Call):
        return False
    function = value.func
    if isinstance(function, ast.Name):
        named = function.id in functions
    elif isinstance(function, ast.Attribute) and function.attr == "extend_path":
        owner = function.value
        by_name = isinstance(owner, ast.Name) and owner.id in modules
        named = by_name or _imports_pkgutil(owner)
    else:
        named = False
    if not named or len(value.args) > 2:
        return False
    arguments: dict[str | None, ast.expr] = {}
    for key, argument in zip(("path", "name"), value.args):
        arguments[key] = argument
    for keyword in value.keywords:
        arguments[keyword.arg] = keyword.value
    return (
        len(arguments) == 2
        and _is_name(arguments.get("path"), "__path__")
        and _is_name(arguments.get("name"), "__name__")
    )


def _imports_pkgutil(expression: ast.expr) -> bool:
    """Return whether an expression is ``__import__("pkgutil")`` or its importlib form."""
    if not isinstance(expression, ast.Call) or _dotted(expression.func) not in _IMPORT_CALLS:
        return False
    return (
        len(expression.args) == 1
        and isinstance(expression.args[0], ast.Constant)
        and expression.args[0].value == "pkgutil"
    )


def _is_name(expression: ast.expr | None, name: str) -> bool:
    """Return whether an expression is the plain name ``name``."""
    return isinstance(expression, ast.Name) and expression.id == name


def _assignments(tree: ast.Module, bindings: Counter[str]) -> dict[str, ast.expr]:
    """Return the value of each name bound only once, by a top-level plain assignment."""
    assignments: dict[str, ast.expr] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if value is not None:
            for name in _target_names(statement):
                if bindings[name] == 1:
                    assignments[name] = value
    return assignments


def _pth_directories(path: str, site: str) -> list[str]:
    """Return the existing directories that the lines of a ``.pth`` file name."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    directories: list[str] = []
    for line in text.splitlines():
        # A line that starts with "import" is code that site runs; it is not run here.
        if not line.strip() or line.startswith(("#", "import ", "import\t")):
            continue
        directory = os.path.abspath(os.path.join(site, line.rstrip()))
        if os.path.isdir(directory):
            directories.append(directory)
    return directories


def _finder_mapping(path: str) -> dict[str, str]:
    """Return the literal ``MAPPING`` of a setuptools editable finder module."""
    try:
        tree = parse_source(Path(path).read_bytes())
    except OSError:
        return {}
    if not isinstance(tree, ast.Module):
        return {}
    for statement in tree.body:
        targets: list[ast.expr]
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if "MAPPING" not in [target.id for target in targets if isinstance(target, ast.Name)]:
            continue
        try:
            mapping = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return {}
        if not isinstance(mapping, dict):
            return {}
        return {
            key: target
            for key, target in mapping.items()
            if isinstance(key, str) and isinstance(target, str)
        }
    return {}


def _editable_root(name: str, target: str) -> str:
    """Return the root of an editable source that maps ``name`` to ``target``."""
    real = os.path.realpath(target)
    parts = PurePath(real).parts
    names = tuple(name.split("."))
    if len(parts) > len(names) and parts[-len(names) :] == names:
        return str(PurePath(*parts[: -len(names)]))
    return os.path.dirname(real)


@cache
def _standard_library() -> tuple[str, ...]:
    """Return the resolved standard library directories of this interpreter."""
    paths = sysconfig.get_paths()
    found = [os.path.realpath(paths[key]) for key in ("stdlib", "platstdlib") if key in paths]
    return tuple(dict.fromkeys(found))


def _installed(real: str) -> bool:
    """Return whether a resolved path is under the standard library or site-packages."""
    if not _INSTALLED_NAMES.isdisjoint(PurePath(real).parts):
        return True
    return any(_within(real, directory) for directory in _standard_library())


def _within(path: str, directory: str) -> bool:
    """Return whether ``path`` is ``directory`` or below it."""
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def _file_sha(path: str, memo: _Memo | None) -> tuple[str, bytes | None]:
    """Return the SHA-256 of a file's bytes, and the bytes if they were read in this call."""
    status = os.stat(path)
    stat = (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )
    remembered = None if memo is None else memo.get(path)
    if remembered is not None and remembered[0] == stat:
        return remembered[1], remembered[2]
    seen = _seen.get(path)
    if seen is not None and seen.trusted and seen.stat == stat:
        return seen.sha, None
    now = time.time_ns()
    data = Path(path).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    # An edit within the file system's timestamp resolution can leave the stat unchanged, so
    # the stat is trusted only for a file whose last change is well before it was read.
    trusted = max(status.st_mtime_ns, status.st_ctime_ns) < now - _RACY_NS
    _seen[path] = _Seen(stat, sha, trusted)
    if memo is not None:
        memo[path] = (stat, sha, data)
    return sha, data


def _python_content(path: str, sha: str, data: bytes | None) -> _Content:
    """Return the code digest and scan of a Python file whose bytes have SHA-256 ``sha``."""
    key = (sha, sys.get_int_max_str_digits())
    content = _contents.get(key)
    if content is None:
        if data is None:
            data = Path(path).read_bytes()
            key = (hashlib.sha256(data).hexdigest(), key[1])
        code = python_code_hash(data)
        tree = parse_source(data) if code.parsed else None
        scan = _scan(tree) if isinstance(tree, ast.Module) else None
        content = _Content(code.digest, scan)
        _contents[key] = content
    return content


def _unchanged(listed: Iterable[tuple[str, str]], memo: _Memo) -> bool:
    """Return whether every listed file still has the bytes it had when it was listed."""
    try:
        return all(
            _file_sha(path, memo if path.endswith(_SOURCE) else None)[0] == sha
            for path, sha in listed
        )
    except OSError:
        return False


def _state(part: CodePart, target: str | None) -> str:
    """Return the hash of the canonical text of an established code part and a target."""
    text = json.dumps(
        [_STATE_VERSION, target, sorted(part.files.items())],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _not_established() -> str:
    """Return a state that matches no other, for a code part that is not established."""
    return f"not established: {uuid.uuid4()}"
