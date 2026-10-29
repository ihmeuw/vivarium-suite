"""Load a pipeline definition by file path or module name.

A pipeline definition is a Python file or module with a function that takes no
arguments and returns a :class:`~vivarium.workflow.frontend.pipeline.Pipeline`. The
function is named ``pipeline`` unless the spec names another one. Loading a definition
imports it and calls that function; it builds nothing and writes no pytask state.

A file is imported as a top-level module named by its stem, with its directory on
``sys.path``, so a step function defined in the file can be imported again by reference
in another process. A definition that lives inside a package should be loaded by its
dotted module name instead, because a file loaded by path cannot use relative imports.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from vivarium.workflow.frontend.pipeline import Pipeline

DEFAULT_FUNCTION = "pipeline"
"""Name of the function :func:`load_pipeline` calls when the spec names none."""


class DefinitionError(Exception):
    """A definition spec names no loadable file, module, or pipeline function."""


def load_pipeline(spec: str) -> Pipeline:
    """Import the definition ``spec`` names and return the pipeline its function builds.

    Parameters
    ----------
    spec
        ``path/to/file.py`` or ``package.module``, optionally followed by
        ``:function``; the function defaults to :data:`DEFAULT_FUNCTION`. When the text
        after the last ``:`` is not a Python identifier, the whole spec is the file or
        module. The file or module part is a file path when it ends in ``.py`` or
        contains ``/``, and a dotted module name otherwise. A relative file path is
        relative to the current directory.

        A file is imported as a module named by its stem, and its directory is added to
        the front of ``sys.path`` unless it is already there. Each call imports the file
        again, so an edited file is picked up. If importing the file raises, no module of
        that name is left in ``sys.modules``. A module named by dotted name is imported
        with :func:`importlib.import_module`, so a module that is already imported is
        not imported again. ``sys.path`` is not changed for it, so the module must be
        installed or on ``PYTHONPATH`` unless the current directory is already on
        ``sys.path``.

    Returns
    -------
        The :class:`~vivarium.workflow.frontend.pipeline.Pipeline` the function returns.

    Raises
    ------
    DefinitionError
        If the file does not exist, a different module with the file's stem as its name
        is already imported (the message says to rename the file), the dotted module or
        one of its parent packages does not exist, the module has no attribute of the
        function's name, that attribute is not callable, or the function returns
        something other than a ``Pipeline``. The message is one line and names the
        spec.
    Exception
        Any other exception raised while importing the definition or calling its
        function, including an import error raised inside the module, propagates
        unchanged.
    """
    source, colon, function_name = spec.rpartition(":")
    if not colon or not function_name.isidentifier():
        source, function_name = spec, DEFAULT_FUNCTION
    if source.endswith(".py") or "/" in source:
        module = _import_file(spec, source)
    else:
        module = _import_module(spec, source)
    try:
        function: object = getattr(module, function_name)
    except AttributeError:
        raise DefinitionError(
            f"Definition {spec!r} has no function named {function_name!r}."
        ) from None
    if not callable(function):
        raise DefinitionError(
            f"Definition {spec!r}: {function_name!r} is a {type(function).__name__}, not a "
            "function."
        )
    pipeline = function()
    if not isinstance(pipeline, Pipeline):
        raise DefinitionError(
            f"Definition {spec!r}: {function_name}() returned {type(pipeline).__name__}, "
            "not a Pipeline."
        )
    return pipeline


def _import_file(spec: str, source: str) -> ModuleType:
    """Import the file ``source`` afresh as a top-level module named by its stem."""
    path = Path(source).resolve()
    if not path.is_file():
        raise DefinitionError(f"Definition {spec!r}: there is no file at {path}.")
    name = path.stem
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or path.suffix != ".py":
        raise DefinitionError(f"Definition {spec!r}: {path} is not a Python file (.py).")
    loaded = sys.modules.get(name)
    if loaded is not None and not _is_loaded_from(loaded, path):
        raise DefinitionError(
            f"Definition {spec!r}: a different module named {name!r} is already imported, "
            f"so {path.name} cannot be imported under that name; rename the file."
        )
    directory = str(path.parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        # Compile from source instead of calling the loader: the loader reuses a cached
        # .pyc when an edit keeps the file's size and modification second.
        code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _is_loaded_from(module: ModuleType, path: Path) -> bool:
    """Return whether ``module`` was imported from the file at ``path``."""
    file = getattr(module, "__file__", None)
    return isinstance(file, str) and Path(file).resolve() == path


def _import_module(spec: str, source: str) -> ModuleType:
    """Import the dotted module ``source``; raise DefinitionError if it is missing."""
    if "" in source.split("."):
        raise DefinitionError(f"Definition {spec!r} names no file or dotted module.")
    try:
        return importlib.import_module(source)
    except ModuleNotFoundError as error:
        missing = error.name
        if missing is None or not (source == missing or source.startswith(f"{missing}.")):
            raise
        raise DefinitionError(
            f"Definition {spec!r}: there is no module named {missing!r}."
        ) from None
