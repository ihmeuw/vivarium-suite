"""Author a pipeline step as a Jupyter notebook run by papermill.

:func:`notebook_step` compiles one notebook into a single backend
:class:`~vivarium.dagger.backend.task.Task` whose ``run`` is a shell command. The
command runs ``python -m papermill`` in the step's declared environment, which
:func:`~vivarium.dagger.backend.build.run_command` puts first on ``PATH``. The source
notebook is a declared input file. The executed notebook, written to its own path, and
the data files named in ``produces`` are the step's products, registered through the
shared :class:`~vivarium.dagger.backend.wiring.ArtifactRegistry` so downstream steps
get real edges to them. If the notebook raises, the step fails and the executed notebook,
with the error in it, stays at its path. The command removes an executed notebook left by
an earlier run before the notebook runs, so an executed notebook found after a failure is
always this run's. :func:`describe_notebook_failure` reads the error back from it for the
step's failure message.

Papermill injects parameters into the notebook's cell tagged ``parameters``. Each input
and product reaches the notebook as its resolved path, as a ``str``, under its parameter
name, and the notebook reads and writes those paths itself. Scalar parameters keep their
Python type, so the string ``"123"`` stays a string and is not read as a number. Every
name a step passes in ``inputs``, ``produces`` or ``parameters`` must be assigned at the
top level of the notebook's first cell tagged ``parameters``, by a plain assignment such
as ``location = None`` or an annotated one such as ``location: str``, and a notebook with
no cell tagged ``parameters`` takes no names. :func:`notebook_step` checks this each time
the step is defined, so a misspelled name fails at once and the notebook does not run on
its own default. The check reads the notebook as JSON with the standard library, so the
pipeline's own environment needs neither papermill nor nbformat.

The notebook always runs on the declared environment's own Python. At run time the
command creates a temporary kernelspec named :data:`KERNEL_NAME` for that environment's
interpreter, sets ``JUPYTER_PATH`` to the directory that holds it, runs papermill with it
on the notebook as Python, and removes it afterwards, also when the notebook fails.
Kernelspecs installed for the user or the system, and the kernel and language named in the
notebook's metadata, are not used. The declared environment therefore needs ``papermill``
and ``ipykernel`` installed; the pipeline's own environment needs neither.

The command, including every parameter value and path, is hashed with the step, so a
changed parameter marks the step stale. Code identity defaults to always-stale, as for
:func:`~vivarium.dagger.frontend.python_step.python_step`, so in this cut a notebook
step re-runs on every build.
"""

import ast
import difflib
import json
import keyword
import numbers
import shlex
import subprocess
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from pytask import PathNode

from vivarium.dagger.backend.build import CODE_ID_KEY, COMMAND_KEY, describe_exit
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactKey, ArtifactRegistry, WiringError

NotebookParameter = str | int | float | bool | None
"""A scalar notebook parameter, which reaches the notebook with this Python type.

:func:`notebook_step` also accepts any other real number, such as a numpy ``int64``, and
passes it as an ``int`` or ``float``; a type-checked caller converts it with ``int()`` or
``float()`` first.
"""

KERNEL_NAME = "vivarium-dagger"
"""The name of the kernelspec a notebook step creates at run time and runs on."""

_NOTEBOOK_INPUT = "__notebook__"
"""Task input name under which the source notebook's file node is attached."""

_EXECUTED_OUTPUT = "__executed__"
"""Task output name under which the executed notebook's node is attached."""

_RESERVED_NAMES = frozenset({_NOTEBOOK_INPUT, _EXECUTED_OUTPUT, CODE_ID_KEY, COMMAND_KEY})
"""Names a notebook parameter cannot take, because the task or the backend uses them."""

_PARAMETERS_TAG = "parameters"
"""The cell tag papermill injects parameters after, in the first cell that carries it."""

_LISTED_NAMES = 10
"""The most names one definition-time error lists before it counts the rest."""


def notebook_step(
    notebook: Path | str,
    *,
    executed: ArtifactKey,
    registry: ArtifactRegistry,
    env: str,
    inputs: Mapping[str, ArtifactKey] | None = None,
    produces: Mapping[str, ArtifactKey] | None = None,
    parameters: Mapping[str, NotebookParameter] | None = None,
    resources: dict[str, Any] | None = None,
    name: str | None = None,
) -> Task:
    """Compile a papermill run of ``notebook`` into one task wired through ``registry``.

    Parameters
    ----------
    notebook
        Path to the source notebook, a ``.ipynb`` file that must exist. It is a declared
        input of the step and is never written; the notebook runs with its own directory
        as the working directory.
    executed
        The artifact key for the executed notebook, which must end in ``.ipynb``.
        Registered with ``registry`` as a product.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    env
        The environment the notebook runs in: a conda env or venv name, or a path to its
        prefix. It must have ``papermill`` and ``ipykernel`` installed. Resolved when the
        pipeline is built or previewed, not here. Pass ``sys.prefix`` to run the notebook
        in the pipeline's own environment.
    inputs
        Maps notebook parameter names to the artifact keys the registry resolves to the
        producing step's node (or a declared root input). Each input's resolved path
        reaches the notebook as a ``str`` under its parameter name.
    produces
        Maps notebook parameter names to artifact keys for the data files the notebook
        writes. Each key is registered with ``registry`` as a product, and its path reaches
        the notebook as a ``str`` under its parameter name. The parent directory of each
        product, and of the executed notebook, is created before the notebook runs.
        Each key must resolve to its own path, distinct from ``executed``'s and
        ``notebook``'s.
    parameters
        Maps notebook parameter names to scalar values. A ``str`` arrives as a ``str``,
        a ``bool`` or ``None`` as itself, and any other integral or real number, such as
        a numpy ``int64``, as a Python ``int`` or ``float``.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster bridge to
        read later.
    name
        The step's name, which becomes the task name. Defaults to ``executed``.

    Returns
    -------
        One :class:`~vivarium.dagger.backend.task.Task` whose ``run`` is the papermill
        command, whose ``env`` is ``env``, whose inputs are the source notebook's file node
        and each input's resolved node (the producing step's own node for a produced key,
        or a node at the file's path for a root input), and whose outputs are the
        registry's nodes for ``executed`` and for each ``produces`` key. Its inputs and
        outputs are keyed by parameter name, with the source notebook under
        ``"__notebook__"`` and the executed notebook under ``"__executed__"``. The command
        lists parameters in name order, so equal arguments give an identical command.

    Raises
    ------
    vivarium.dagger.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or ``executed`` or a
        ``produces`` key is a declared root input or resolves to the path of a different
        key that is already registered (another step's output) or of a root input. The
        message starts with ``Step '<name>':``, followed by the registry's message, which
        names the artifact key. The same key claimed by two steps is left for the build
        to reject.
    ValueError
        If ``name`` or ``env`` is empty; a parameter name in ``inputs``, ``produces`` or
        ``parameters`` is not a valid Python identifier, is a keyword, appears in more
        than one of the three, or is reserved (``__notebook__``, ``__executed__``,
        ``__code_id__``, ``__command__``); a parameter value is not a ``str``, a real
        number, a ``bool`` or ``None`` (the message says to pass files through ``inputs``
        and to convert a numpy ``bool_`` with ``bool()``); ``notebook`` does not end in
        ``.ipynb`` or does not exist; ``executed`` does not end in ``.ipynb``; or two of
        ``notebook``, ``executed`` and the ``produces`` keys resolve to one path, as when a
        ``produces`` key equals ``executed`` or is given twice (the message names both).
        When ``inputs``, ``produces`` and ``parameters`` hold at least one name between
        them, also if ``notebook`` cannot be read as an nbformat 4 notebook, has no cell
        tagged ``parameters``, or its first cell tagged ``parameters`` is not a code cell,
        is not valid Python, or does not assign every passed name at its top level. Each
        of these messages starts with ``Step '<name>':`` and names the notebook. A name
        the cell does not assign is listed with the argument it came from and, when one
        is close, a name the cell does assign. A message lists at most ten names, in
        sorted order, followed by ``, and <N> more``. Every one of these is raised before
        anything is registered.
    """
    task_name = executed if name is None else name
    if not task_name:
        raise ValueError(f"Task name must be a non-empty string, got {task_name!r}.")
    if not env:
        raise ValueError(f"Task '{task_name}': env must be a non-empty string, got {env!r}.")
    input_keys: dict[str, ArtifactKey] = dict(inputs or {})
    product_keys: dict[str, ArtifactKey] = dict(produces or {})
    scalars: dict[str, NotebookParameter] = dict(parameters or {})
    groups: dict[str, Iterable[str]] = {
        "inputs": input_keys,
        "produces": product_keys,
        "parameters": scalars,
    }
    _check_names(task_name, groups)
    _check_parameter_values(task_name, scalars)
    source = Path(notebook).resolve()
    _check_paths(task_name, source, executed)
    _check_collisions(task_name, source, executed, product_keys, registry)
    _check_parameters_cell(task_name, source, groups)

    try:
        dependencies = {
            param: registry.resolve_input(key) for param, key in input_keys.items()
        }
        executed_node = registry.register_output(executed)
        products = {
            param: registry.register_output(key) for param, key in product_keys.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    values: dict[str, NotebookParameter] = {
        param: str(node.path) for param, node in (*dependencies.items(), *products.items())
    }
    values.update(scalars)
    command = _papermill_command(
        source,
        registry.path_for(executed),
        [registry.path_for(key) for key in product_keys.values()],
        values,
    )
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=command,
        inputs={_NOTEBOOK_INPUT: file_node(source), **dependencies},
        outputs={_EXECUTED_OUTPUT: executed_node, **products},
        resources={} if resources is None else resources,
        env=env,
    )


def describe_notebook_failure(task: Task, error: BaseException) -> str | None:
    """Describe why a notebook step failed, from its executed notebook on disk.

    The executed notebook is read as JSON with the standard library, so the pipeline's
    own environment needs neither papermill nor nbformat.
    :func:`~vivarium.dagger.frontend.pipeline.run` uses this description in the message
    of :class:`~vivarium.dagger.frontend.pipeline.StepFailed`.

    Parameters
    ----------
    task
        A task that failed. Only a task built by :func:`notebook_step` is described.
    error
        The exception the task failed with.

    Returns
    -------
        For a notebook step whose executed notebook records an error output, the first
        one in cell order, whatever the type of ``error``::

            notebook <file> raised <ename>: <evalue> in cell In [<n>]. The executed
            notebook, with the error, is at <path>.

        on one line, where ``<file>`` is the source notebook's file name, ``<ename>``
        is the error output's name, ``<evalue>`` is the first line of its value that is
        not blank, without the whitespace around it, ``<n>`` is the failing cell's
        execution count, and ``<path>`` is the executed notebook's absolute path. The
        rest of a value that has more than one line is only in the executed notebook.
        ``: <evalue>`` is left out when the value has no line that is not blank, and
        `` in cell In [<n>]`` when the cell has no execution count.

        For a notebook step whose executed notebook is missing, cannot be read or
        parsed, or records no error, when ``error`` is a
        :class:`subprocess.CalledProcessError`::

            notebook <file> left no error in an executed notebook at <path>, and <exit>;
            its output is in pytask's report for the step.

        on one line, where ``<exit>`` is
        :func:`~vivarium.dagger.backend.build.describe_exit` of the error's return code.

        ``None`` for a task that is not a notebook step, and for a notebook step whose
        executed notebook records no error when ``error`` is not a
        ``CalledProcessError``, as when the notebook finished but did not write a
        declared product.
    """
    if not isinstance(task.run, str):
        return None
    source = _node_path(task.inputs.get(_NOTEBOOK_INPUT))
    executed = _node_path(task.outputs.get(_EXECUTED_OUTPUT))
    if source is None or executed is None:
        return None
    recorded = _first_error(executed)
    if recorded is not None:
        ename, evalue, count = recorded
        line = _first_line(evalue)
        raised = f"{ename}: {line}" if line else ename
        cell = "" if count is None else f" in cell In [{count}]"
        return (
            f"notebook {source.name} raised {raised}{cell}. The executed notebook, with "
            f"the error, is at {executed}."
        )
    if isinstance(error, subprocess.CalledProcessError):
        return (
            f"notebook {source.name} left no error in an executed notebook at {executed}, "
            f"and {describe_exit(error.returncode)}; its output is in pytask's report for "
            "the step."
        )
    return None


def _node_path(node: object) -> Path | None:
    """Return ``node``'s path if it is a PathNode on a :class:`pathlib.Path`."""
    if isinstance(node, PathNode) and isinstance(node.path, Path):
        return node.path
    return None


def _first_error(executed: Path) -> tuple[str, str, int | None] | None:
    """Return the first error output's name, value and cell execution count, if any."""
    try:
        notebook = json.loads(executed.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    cells = notebook.get("cells") if isinstance(notebook, dict) else None
    if not isinstance(cells, list):
        return None
    for cell in cells:
        outputs = cell.get("outputs") if isinstance(cell, dict) else None
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            if isinstance(output, dict) and output.get("output_type") == "error":
                count = cell.get("execution_count")
                has_count = isinstance(count, int) and not isinstance(count, bool)
                return (
                    str(output.get("ename") or "an error"),
                    str(output.get("evalue") or ""),
                    count if has_count else None,
                )
    return None


def _first_line(text: str) -> str:
    """Return the first line of ``text`` that is not blank, stripped, or ``""``."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _check_names(task_name: str, groups: Mapping[str, Iterable[str]]) -> None:
    """Reject a name that is not an identifier, is a keyword or reserved, or is used twice."""
    seen: dict[str, str] = {}
    for group, names in groups.items():
        for name in names:
            if not name.isidentifier():
                problem = "is not a valid Python identifier"
            elif keyword.iskeyword(name):
                problem = "is a Python keyword"
            elif name in _RESERVED_NAMES:
                reserved = ", ".join(repr(each) for each in sorted(_RESERVED_NAMES))
                problem = f"is reserved; the reserved names are {reserved}"
            elif name in seen:
                problem = (
                    f"also appears in {seen[name]}; a name may appear in only one of "
                    "inputs, produces and parameters"
                )
            else:
                seen[name] = group
                continue
            raise ValueError(
                f"Task '{task_name}': parameter name {name!r} in {group} {problem}."
            )


def _check_parameter_values(task_name: str, parameters: Mapping[str, object]) -> None:
    """Reject a parameter value that is not a str, a real number, a bool or None."""
    for name, value in parameters.items():
        if value is None or isinstance(value, (str, bool, numbers.Real)):
            continue
        raise ValueError(
            f"Task '{task_name}': parameter {name!r} has unsupported type "
            f"{_type_name(value)}; a parameter must be a str, a real number, a bool or "
            "None. To give the notebook a file, pass files through inputs=; convert a "
            "numpy scalar such as numpy.bool_ with bool(), int() or float()."
        )


def _type_name(value: object) -> str:
    """Return the name of ``value``'s type, prefixed by its module unless it is a builtin."""
    # Under numpy 2 the bool scalar type's bare name is 'bool', which reads as supported.
    kind = type(value)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def _check_paths(task_name: str, source: Path, executed: ArtifactKey) -> None:
    """Reject a source that is not an existing .ipynb file, or a bad executed key."""
    if source.suffix != ".ipynb":
        raise ValueError(
            f"Task '{task_name}': notebook {str(source)!r} does not end in '.ipynb'."
        )
    if not source.is_file():
        raise ValueError(
            f"Task '{task_name}': notebook {str(source)!r} does not exist or is not a file."
        )
    if Path(executed).suffix != ".ipynb":
        raise ValueError(
            f"Task '{task_name}': executed key {executed!r} does not end in '.ipynb'."
        )


def _check_collisions(
    task_name: str,
    source: Path,
    executed: ArtifactKey,
    products: Mapping[str, ArtifactKey],
    registry: ArtifactRegistry,
) -> None:
    """Reject two of the source, the executed notebook and the products at one path."""
    claimed = {source: f"source notebook {str(source)!r}"}
    outputs = {f"executed key {executed!r}": executed}
    outputs.update(
        (f"product {param!r} (key {key!r})", key) for param, key in products.items()
    )
    for label, key in outputs.items():
        path = registry.path_for(key)
        if path in claimed:
            raise ValueError(
                f"Task '{task_name}': {claimed[path]} and {label} resolve to one path "
                f"{str(path)!r}; each output must be written to its own path."
            )
        claimed[path] = label


def _check_parameters_cell(
    task_name: str, source: Path, groups: Mapping[str, Iterable[str]]
) -> None:
    """Reject a passed name that the notebook's ``parameters`` cell does not assign."""
    passed = {name: group for group, names in groups.items() for name in names}
    if not passed:
        return
    cell = _parameters_cell(task_name, source)
    names = sorted(passed)
    if cell is None:
        raise ValueError(
            f"Step '{task_name}': notebook {str(source)!r} has no cell tagged 'parameters', "
            f"so papermill would inject {_listed([repr(name) for name in names])} at the "
            "top of the notebook, where a later cell can overwrite what it injects. Add a "
            "code cell tagged 'parameters' that assigns each name, such as "
            f"{names[0]} = None."
        )
    try:
        assigned = _assigned_names(cell)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        # ast.parse raises RecursionError or MemoryError for deeply nested code, and
        # ValueError for a null byte on some older releases.
        raise ValueError(_invalid_python(task_name, source, error)) from None
    missing = [name for name in names if name not in assigned]
    if not missing:
        return
    candidates = sorted(assigned)
    unassigned = [_unassigned(name, passed[name], candidates) for name in missing]
    raise ValueError(
        f"Step '{task_name}': the parameters cell of notebook {str(source)!r} does not "
        f"assign {_listed(unassigned)}. papermill would inject each one as a variable the "
        "notebook never reads. Fix the name in the step, or assign it in that cell with a "
        f"plain top-level assignment such as {missing[0]} = None; assignments in loops, "
        "conditions or through globals() are not counted."
    )


def _unassigned(name: str, group: str, candidates: Sequence[str]) -> str:
    """Describe an unassigned name with its argument and the closest assigned name."""
    close = difflib.get_close_matches(name, candidates, n=1)
    suggestion = f"; did you mean {close[0]!r}?" if close else ""
    return f"{name!r} ({group}{suggestion})"


def _invalid_python(task_name: str, source: Path, error: Exception) -> str:
    """Describe a ``parameters`` cell that ``ast.parse`` rejects with ``error``."""
    if isinstance(error, SyntaxError):
        line = "" if error.lineno is None else f" at line {error.lineno}"
        problem = f"{line}: {error.msg}"
    else:
        problem = f": {str(error) or type(error).__name__}"
    return (
        f"Step '{task_name}': the parameters cell of notebook {str(source)!r} is not valid "
        f"Python{problem}; keep only plain assignments in that cell, and move magics, "
        "shell commands and other code to a later cell."
    )


def _parameters_cell(task_name: str, source: Path) -> str | None:
    """Return the source of the first cell tagged ``parameters``, or ``None`` if none is."""
    try:
        notebook = json.loads(source.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise _unreadable(task_name, source, f"it is not UTF-8 text ({error})") from None
    except json.JSONDecodeError as error:
        raise _unreadable(task_name, source, f"it is not JSON ({error})") from None
    except RecursionError:
        raise _unreadable(task_name, source, "its JSON is nested too deeply") from None
    except OSError as error:
        raise _unreadable(task_name, source, f"it could not be read ({error})") from None
    if not isinstance(notebook, dict):
        raise _unreadable(task_name, source, "its top level is not a JSON object")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise _unreadable(
            task_name, source, "it has no top-level 'cells' list, as nbformat 4 requires"
        )
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise _unreadable(task_name, source, f"cells[{index}] is not a JSON object")
        metadata = cell.get("metadata", {})
        if not isinstance(metadata, dict):
            raise _unreadable(
                task_name, source, f"the metadata of cells[{index}] is not a JSON object"
            )
        tags = metadata.get("tags", [])
        if not isinstance(tags, list):
            raise _unreadable(task_name, source, f"the tags of cells[{index}] are not a list")
        if _PARAMETERS_TAG not in tags:
            continue
        cell_type = cell.get("cell_type")
        if cell_type != "code":
            raise ValueError(
                f"Step '{task_name}': the first cell tagged 'parameters' in notebook "
                f"{str(source)!r}, cells[{index}], has cell_type {cell_type!r}, but it must "
                "be a code cell, because papermill injects the parameters after it. Make "
                "it a code cell that assigns each name the step passes."
            )
        text = cell.get("source")
        if isinstance(text, list) and all(isinstance(line, str) for line in text):
            return "".join(text)
        if isinstance(text, str):
            return text
        raise _unreadable(
            task_name,
            source,
            f"the source of cells[{index}], the first cell tagged 'parameters', is not a "
            "string or a list of strings",
        )
    return None


def _unreadable(task_name: str, source: Path, reason: str) -> ValueError:
    """Return the error for a source notebook that cannot be read, for ``reason``."""
    return ValueError(
        f"Step '{task_name}': notebook {str(source)!r} cannot be read as a notebook: "
        f"{reason}."
    )


def _assigned_names(cell: str) -> set[str]:
    """Return the names bound by ``cell``'s top-level plain and annotated assignments."""
    names: set[str] = set()
    # A SyntaxWarning (such as "C:\data") must neither print nor, under -W error, reject a
    # cell that runs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(cell)
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                names.update(_target_names(target))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
    return names


def _target_names(target: ast.expr) -> Iterator[str]:
    """Yield each plain name that the assignment target ``target`` binds."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _listed(items: Sequence[str]) -> str:
    """Join the first ``_LISTED_NAMES`` of ``items`` and count the rest."""
    shown = items[:_LISTED_NAMES]
    rest = len(items) - len(shown)
    listed = ", ".join(shown)
    return f"{listed}, and {rest} more" if rest > 0 else listed


def _parameter_flag(name: str, value: NotebookParameter) -> list[str]:
    """Return papermill's arguments that inject ``value`` as ``name``, keeping its type."""
    if value is None or isinstance(value, bool):
        # A JSON key is quoted, so papermill's YAML 1.1 reader keeps a name like 'on' a str.
        return ["-y", json.dumps({name: value})]
    if isinstance(value, str):
        return ["-r", name, value]
    if isinstance(value, numbers.Integral):
        return ["-p", name, str(int(value))]
    # float() first, since numpy 2 gives repr(np.float64(0.5)) as 'np.float64(0.5)'.
    return ["-p", name, repr(float(value))]


def _escape_braces(path: Path) -> str:
    """Return ``path`` with its braces doubled, since papermill applies str.format to it."""
    return str(path).replace("{", "{{").replace("}", "}}")


def _papermill_command(
    notebook: Path,
    executed: Path,
    products: Iterable[Path],
    values: Mapping[str, NotebookParameter],
) -> str:
    """Return the command that makes output dirs, removes a previous executed notebook,
    and runs papermill on a temporary kernel."""
    directories = sorted({str(path.parent) for path in (executed, *products)})
    flags = [arg for name in sorted(values) for arg in _parameter_flag(name, values[name])]
    papermill = shlex.join(
        ["python", "-m", "papermill", _escape_braces(notebook), _escape_braces(executed)]
        + ["-k", KERNEL_NAME, "--language", "python"]
        + flags
        + ["--cwd", str(notebook.parent)]
    )
    # Bare `python` keeps an absolute interpreter path out of the hashed command. TMPDIR
    # keeps ipykernel's staging dir under $tmp so the trap removes it. JUPYTER_PATH is
    # exported before the install, which otherwise warns the kernelspec may not be found.
    return " && ".join(
        [
            f"mkdir -p {shlex.join(directories)}",
            f"rm -f {shlex.quote(str(executed))}",
            "tmp=$(mktemp -d)",
            "trap 'rm -rf \"$tmp\"' EXIT",
            'export JUPYTER_PATH="$tmp/share/jupyter"',
            'TMPDIR="$tmp" python -m ipykernel install --prefix "$tmp" --name '
            f"{shlex.quote(KERNEL_NAME)} > /dev/null",
            papermill,
        ]
    )
