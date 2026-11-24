"""The code part of a step's identity: the first-party files that the step imports.

A step's code part is the set of first-party files that its starting point imports,
directly or through other files, each with a hash of its code. The import analysis
reads the files and imports nothing. It follows every ``import`` statement at any depth,
including imports inside functions, but not imports under ``if TYPE_CHECKING:`` and not
imports inside the ``if __name__ == "__main__":`` block of a file other than the starting
point. It resolves each name against a search path, in order, as the interpreter would.

First-party code is code in the pipeline's declared repository root, or in the source of
an editable install. An editable install is recognised in either of the two forms that
setuptools writes into a ``site-packages`` directory on the search path: a ``.pth`` file
that adds a source directory to the search path (compat mode), or an editable finder
module, ``__editable___*_finder.py``, whose literal ``MAPPING`` maps each top-level
package to its source directory, which an import of that package then resolves to.
Neither form is imported or run. Code that the environment installs,
including anything under a ``site-packages`` or ``dist-packages`` directory or a virtual
environment inside the repository, is not first-party; it belongs to the environment
part of the identity, and the analysis stops there. The standard library and built-in
modules are installed code.

A module that opens a data file while it is imported, at the module's top level, by a
path built only from the module's own location (``__file__``) and string literals, adds
that data file to the code part. A Python file is hashed with
:func:`~vivarium.dagger.backend.code_hash.python_file_hash`, and a data file by its bytes.
Each file is named by its path relative to the root that holds it, so the same content
in two checkouts at different paths gives the same code part.

The code part is not established when the starting point is outside every first-party
root, or when an import resolves to a file that is neither first-party nor installed.
A node whose state is not established makes its step run on every build.

"""

import ast
import hashlib
import json
import os
import sys
import sysconfig
import time
import uuid
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.machinery import BYTECODE_SUFFIXES, EXTENSION_SUFFIXES, SOURCE_SUFFIXES
from pathlib import Path, PurePath
from typing import Any

from vivarium.dagger.backend.code_hash import _PARSE_ERRORS, _warnings_lock, python_file_hash


@dataclass(frozen=True)
class CodePart:
    """The first-party files of a step's code part, or the reason it is not established."""

    files: Mapping[str, str]
    """Each first-party file's name, relative to the root that holds it, mapped to the
    digest of its code (a Python file) or of its bytes (a data file). Empty when the code
    part is not established."""
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
        The code part. It is not established when ``start`` is outside every
        first-party root, or when an import resolves to a file outside the first-party
        roots that is not installed code. A first-party file that cannot be parsed is
        listed with its bytes hash, and the analysis follows no imports from it.
    """
    start_path = os.path.abspath(start)
    root = None if repository_root is None else os.path.realpath(repository_root)
    directories = tuple(os.path.abspath(entry) for entry in search_path)
    key = (start_path, root, directories, sys.get_int_max_str_digits())
    cached = _analyses.get(key)
    if cached is not None and _unchanged(cached.listed):
        return cached.part
    walk = _Walk(root, directories)
    part = walk.run(start_path)
    if part.established:
        _analyses[key] = _Analysis(part, tuple(walk.listed))
    else:
        _analyses.pop(key, None)
    return part


@dataclass
class CodeIdentityNode:
    """A step's identity node, whose state is a hash of the step's code part.

    The node implements pytask's node protocol. Its state is computed from
    :func:`code_part` for the file ``start``, with the pipeline's ``repository_root`` and
    the build process's :data:`sys.path`. When the code part is not established, the state
    is a new string on every call, so the step runs on every build. The state is never
    ``None`` or empty, and :meth:`state` never raises. Within one process the code part of
    one starting point is analysed once for each content of the files it lists.

    Two nodes with the same ``name``, ``start`` and ``repository_root`` are equal.
    """

    name: str
    """Identifier of the node, unique within its task, such as ``"<step>::code"``."""
    start: Path
    """The resolved Python file the step's code starts in."""
    repository_root: Path | None = None
    """The pipeline's resolved repository root, or ``None`` when it declares none."""
    attributes: dict[Any, Any] = field(default_factory=dict, compare=False)
    """pytask node attributes; unused, kept to satisfy the node protocol."""

    @property
    def signature(self) -> str:
        """Return a hash of the node's name, which is stable across processes."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def state(self) -> str:
        """Return a hash of the code part, or a new string when it is not established."""
        # A failed analysis makes the step run, rather than failing the build.
        try:
            part = code_part(self.start, self.repository_root, list(sys.path))
        except Exception:
            return _not_established()
        return _state(part) if part.established else _not_established()

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
"""The prefix of a data file's digest."""

_STATE_VERSION = "code-part-1"
"""The version of the canonical text that a node's state hashes."""

_RACY_NS = 2_000_000_000
"""How much older than its reading a file's last change must be for its stat to be trusted."""

_MODULE_SUFFIXES = (*EXTENSION_SUFFIXES, *SOURCE_SUFFIXES, *BYTECODE_SUFFIXES)
"""The suffixes of module files, in the order the import system tries them."""

_OPEN_CALLS = frozenset({"open", "io.open", "codecs.open"})
"""Calls that open the file their first argument names."""

_PATH_READS = frozenset({"open", "read_text", "read_bytes"})
"""Path methods that open or read the file of the path they are called on."""

_PATH_TYPES = frozenset({"Path", "pathlib.Path", "PurePath", "pathlib.PurePath"})

_SAME_PATH_CALLS = frozenset(
    {"str", "os.fspath", "os.path.abspath", "os.path.realpath", "os.path.normpath"}
)
"""Calls that return the path their one argument names."""

_MAX_DEPTH = 32
"""The deepest path expression that is computed."""


@dataclass(frozen=True)
class _Import:
    """An import statement's module, imported names, relative level and block."""

    module: str
    names: tuple[str, ...]
    level: int
    main_only: bool


@dataclass(frozen=True)
class _Read:
    """The path expression of a file that a module opens at its top level."""

    path: ast.expr
    main_only: bool


@dataclass(frozen=True)
class _Scan:
    """The imports, top-level opened paths and module-level assignments of a file."""

    imports: tuple[_Import, ...]
    reads: tuple[_Read, ...]
    assignments: Mapping[str, ast.expr | None]


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
    """A module's source file, if it has one, and its search path if it is a package."""

    file: str | None
    path: tuple[str, ...] | None


@dataclass(frozen=True)
class _Resolved:
    """The source files that an import loads, and the last package's search path."""

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

_analyses: dict[tuple[str, str | None, tuple[str, ...], int], _Analysis] = {}
"""The last established code part of each starting file, root, search path and limit."""


class _Walk:
    """One analysis of the first-party files that a starting file imports."""

    def __init__(self, root: str | None, search_path: Sequence[str]) -> None:
        self.root = root
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
                    self.editable_roots.append(os.path.dirname(os.path.realpath(target)))
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
            sha, data = _file_sha(file)
            content = _python_content(file, sha, data)
        except OSError:
            role = "starting" if is_start else "first-party"
            self.reason = f"The {role} file {file} cannot be read."
            return
        self.python[name] = content.digest
        self.listed.append((file, sha))
        if content.scan is None:
            return
        for statement in content.scan.imports:
            if is_start or not statement.main_only:
                self._follow(statement, file)
                if self.reason is not None:
                    return
        evaluator = _PathEvaluator(file, content.scan.assignments)
        for read in content.scan.reads:
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
        """Return the source files that importing ``parts`` from ``path`` loads, or None."""
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
        if name is not None and name in self.mapping:
            mapped = _mapped_module(self.mapping[name])
            if mapped is not None:
                return mapped
        portions: list[str] = []
        for directory in path:
            candidate = os.path.join(directory, part)
            is_entry = part in self._listing(directory)
            if is_entry:
                found, init = self._module_file(candidate, "__init__")
                if found:
                    return _Module(init, (candidate,))
            found, source = self._module_file(directory, part)
            if found:
                return _Module(source, None)
            if is_entry and os.path.isdir(candidate):
                portions.append(candidate)
        return _Module(None, tuple(portions)) if portions else None

    def _module_file(self, directory: str, stem: str) -> tuple[bool, str | None]:
        """Return whether ``directory`` has a module file ``stem``, and its source file."""
        listing = self._listing(directory)
        for suffix in _MODULE_SUFFIXES:
            path = os.path.join(directory, stem + suffix)
            if stem + suffix in listing and os.path.isfile(path):
                return True, path if suffix in SOURCE_SUFFIXES else None
        return False, None

    def _add(self, found: str, importer: str) -> None:
        """Queue a file that an import loads, or end the analysis if it is foreign."""
        real = os.path.realpath(found)
        if real in self.visited:
            return
        self.visited.add(real)
        kind, name = self._classify(real)
        if kind == "foreign":
            self.reason = (
                f"{importer} imports {found}, which is {_OUTSIDE} and is not installed code."
            )
        elif kind == "first":
            self.queue.append((found, name, False))

    def _add_data(self, value: _PathValue | None) -> None:
        """List a first-party data file whose path a module builds from its ``__file__``."""
        if value is None or not value.from_file or not os.path.isabs(value.path):
            return
        if not os.path.isfile(value.path):
            return
        kind, name = self._classify(os.path.realpath(value.path))
        if kind != "first" or name in self.data:
            return
        try:
            sha, _ = _file_sha(value.path)
        except OSError:
            return
        self.data[name] = _BYTES + sha
        self.listed.append((value.path, sha))


@dataclass(frozen=True)
class _PathEvaluator:
    """Compute the paths that a module builds from its ``__file__`` and string literals."""

    file: str
    assignments: Mapping[str, ast.expr | None]

    def value(self, expression: ast.expr | None, depth: int = 0) -> _PathValue | None:
        """Return the path that ``expression`` computes, or None."""
        if expression is None or depth > _MAX_DEPTH:
            return None
        if isinstance(expression, ast.Constant):
            text = expression.value
            return _PathValue(text, False) if isinstance(text, str) else None
        if isinstance(expression, ast.Name):
            if expression.id == "__file__":
                return _PathValue(self.file, True)
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


def _syntax_tree(source: bytes) -> ast.Module | None:
    """Return the syntax tree of Python source, whatever the warning filters, or None."""
    try:
        with _warnings_lock, warnings.catch_warnings():
            # As in code_hash: an "error" filter would turn a SyntaxWarning into an error.
            warnings.simplefilter("ignore")
            return ast.parse(source)
    except _PARSE_ERRORS:
        return None


def _scan(source: bytes) -> _Scan | None:
    """Return what the analysis follows in Python source, or None if it does not parse."""
    tree = _syntax_tree(source)
    if tree is None:
        return None
    imports: list[_Import] = []
    reads: list[_Read] = []
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
            elif isinstance(node, ast.ImportFrom):
                names = tuple(alias.name for alias in node.names)
                imports.append(_Import(node.module or "", names, node.level or 0, main_only))
            elif isinstance(node, ast.Call) and not deferred:
                reads += [_Read(path, main_only) for path in _opened_paths(node)]
            body = _deferred_ids(node)
            children = [
                (child, deferred or id(child) in body, main_only)
                for child in ast.iter_child_nodes(node)
            ]
        stack.extend(reversed(children))
    return _Scan(tuple(imports), tuple(reads), _assignments(tree))


def _deferred_ids(node: ast.AST) -> set[int]:
    """Return the ids of the children of ``node`` that do not run when it runs."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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


def _opened_paths(call: ast.Call) -> list[ast.expr]:
    """Return the path expression that a call opens, if it opens one."""
    if _dotted(call.func) in _OPEN_CALLS:
        if call.args:
            return [call.args[0]]
        return [keyword.value for keyword in call.keywords if keyword.arg == "file"]
    if isinstance(call.func, ast.Attribute) and call.func.attr in _PATH_READS:
        return [call.func.value]
    return []


def _assignments(tree: ast.Module) -> dict[str, ast.expr | None]:
    """Return each name assigned at module level to its value, or None if assigned again."""
    assignments: dict[str, ast.expr | None] = {}
    for statement in tree.body:
        target: ast.expr
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target = statement.target
            value = statement.value
        else:
            continue
        if isinstance(target, ast.Name):
            assignments[target.id] = None if target.id in assignments else value
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
        tree = _syntax_tree(Path(path).read_bytes())
    except OSError:
        return {}
    if tree is None:
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


def _mapped_module(target: str) -> _Module | None:
    """Return the module that an editable finder maps a name to, or None."""
    init = os.path.join(target, "__init__.py")
    if os.path.isfile(init):
        return _Module(init, (target,))
    try:
        source = str(PurePath(target).with_suffix(".py"))
    except ValueError:
        return None
    return _Module(source, None) if os.path.isfile(source) else None


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


def _file_sha(path: str) -> tuple[str, bytes | None]:
    """Return the SHA-256 of a file's bytes, and the bytes if this call read them."""
    status = os.stat(path)
    stat = (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )
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
    return sha, data


def _python_content(path: str, sha: str, data: bytes | None) -> _Content:
    """Return the code digest and scan of a Python file whose bytes have SHA-256 ``sha``."""
    key = (sha, sys.get_int_max_str_digits())
    content = _contents.get(key)
    if content is None:
        code = python_file_hash(path)
        scan: _Scan | None = None
        if code.parsed:
            scan = _scan(Path(path).read_bytes() if data is None else data)
        content = _Content(code.digest, scan)
        _contents[key] = content
    return content


def _unchanged(listed: Sequence[tuple[str, str]]) -> bool:
    """Return whether every listed file still has the bytes it had when it was listed."""
    try:
        return all(_file_sha(path)[0] == sha for path, sha in listed)
    except OSError:
        return False


def _state(part: CodePart) -> str:
    """Return the hash of the canonical text of an established code part."""
    text = json.dumps(
        [_STATE_VERSION, sorted(part.files.items())],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _not_established() -> str:
    """Return a state that matches no other, for a code part that is not established."""
    return f"not established: {uuid.uuid4()}"
