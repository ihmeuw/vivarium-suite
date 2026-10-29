"""Author a pipeline step as a Jupyter notebook run by papermill.

:func:`notebook_step` compiles one notebook into a single backend
:class:`~vivarium.workflow.backend.task.Task` whose ``run`` is a shell command. The
command runs ``python -m papermill`` in the step's declared environment, which
:func:`~vivarium.workflow.backend.build.run_command` puts first on ``PATH``. The source
notebook is a declared input file. The executed notebook, written to its own path, and
the data files named in ``produces`` are the step's products, registered through the
shared :class:`~vivarium.workflow.backend.wiring.ArtifactRegistry` so downstream steps
get real edges to them. If the notebook raises, the step fails and the executed notebook,
with the error in it, stays at its path.

Papermill injects parameters into the notebook's cell tagged ``parameters``. Each input
and product reaches the notebook as its resolved path, as a ``str``, under its parameter
name, and the notebook reads and writes those paths itself. Scalar parameters keep their
Python type, so the string ``"123"`` stays a string and is not read as a number.

The notebook always runs on the declared environment's own Python. At run time the
command creates a temporary kernelspec named :data:`KERNEL_NAME` for that environment's
interpreter, sets ``JUPYTER_PATH`` to the directory that holds it, runs papermill with it
on the notebook as Python, and removes it afterwards, also when the notebook fails.
Kernelspecs installed for the user or the system, and the kernel and language named in the
notebook's metadata, are not used. The declared environment therefore needs ``papermill``
and ``ipykernel`` installed; the pipeline's own environment needs neither.

The command, including every parameter value and path, is hashed with the step, so a
changed parameter marks the step stale. Code identity defaults to always-stale, as for
:func:`~vivarium.workflow.frontend.python_step.python_step`, so in this cut a notebook
step re-runs on every build.
"""

import json
import keyword
import numbers
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from vivarium.workflow.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactKey, ArtifactRegistry

NotebookParameter = str | int | float | bool | None
"""A scalar notebook parameter, which reaches the notebook with this Python type.

:func:`notebook_step` also accepts any other real number, such as a numpy ``int64``, and
passes it as an ``int`` or ``float``; a type-checked caller converts it with ``int()`` or
``float()`` first.
"""

KERNEL_NAME = "vivarium-workflow"
"""The name of the kernelspec a notebook step creates at run time and runs on."""

_NOTEBOOK_INPUT = "__notebook__"
"""Task input name under which the source notebook's file node is attached."""

_EXECUTED_OUTPUT = "__executed__"
"""Task output name under which the executed notebook's node is attached."""

_RESERVED_NAMES = frozenset({_NOTEBOOK_INPUT, _EXECUTED_OUTPUT, CODE_ID_KEY, COMMAND_KEY})
"""Names a notebook parameter cannot take, because the task or the backend uses them."""


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
        One :class:`~vivarium.workflow.backend.task.Task` whose ``run`` is the papermill
        command, whose ``env`` is ``env``, whose inputs are the source notebook's file node
        and each input's resolved node (the producing step's own node for a produced key,
        or a node at the file's path for a root input), and whose outputs are the
        registry's nodes for ``executed`` and for each ``produces`` key. Its inputs and
        outputs are keyed by parameter name, with the source notebook under
        ``"__notebook__"`` and the executed notebook under ``"__executed__"``. The command
        lists parameters in name order, so equal arguments give an identical command.

    Raises
    ------
    vivarium.workflow.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or ``executed`` or a
        ``produces`` key is a declared root input or resolves to the path of a different
        key that is already registered (another step's output) or of a root input. The
        same key claimed by two steps is left for the build to reject.
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
        Every one of these is raised before anything is registered.
    """
    task_name = executed if name is None else name
    if not task_name:
        raise ValueError(f"Task name must be a non-empty string, got {task_name!r}.")
    if not env:
        raise ValueError(f"Task '{task_name}': env must be a non-empty string, got {env!r}.")
    input_keys: dict[str, ArtifactKey] = dict(inputs or {})
    product_keys: dict[str, ArtifactKey] = dict(produces or {})
    scalars: dict[str, NotebookParameter] = dict(parameters or {})
    _check_names(
        task_name, {"inputs": input_keys, "produces": product_keys, "parameters": scalars}
    )
    _check_parameter_values(task_name, scalars)
    source = Path(notebook).resolve()
    _check_paths(task_name, source, executed)
    _check_collisions(task_name, source, executed, product_keys, registry)

    dependencies = {param: registry.resolve_input(key) for param, key in input_keys.items()}
    executed_node = registry.register_output(executed)
    products = {param: registry.register_output(key) for param, key in product_keys.items()}
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
    # TODO(MIC-7520): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=command,
        inputs={_NOTEBOOK_INPUT: file_node(source), **dependencies},
        outputs={_EXECUTED_OUTPUT: executed_node, **products},
        resources={} if resources is None else resources,
        env=env,
    )


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
    """Return the command that makes output dirs and runs papermill on a temporary kernel."""
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
            "tmp=$(mktemp -d)",
            "trap 'rm -rf \"$tmp\"' EXIT",
            'export JUPYTER_PATH="$tmp/share/jupyter"',
            'TMPDIR="$tmp" python -m ipykernel install --prefix "$tmp" --name '
            f"{shlex.quote(KERNEL_NAME)} > /dev/null",
            papermill,
        ]
    )
