"""Author a pipeline step as a shell command.

:func:`command_step` compiles one shell command into a single backend
:class:`~vivarium.dagger.backend.task.Task` whose ``run`` is the command string. Use it
when a step needs another environment than the pipeline's, or when the work is already a
command-line tool or a script; otherwise use
:func:`~vivarium.dagger.frontend.python_step.python_step`, which always runs in the
pipeline's own process.

The step's inputs and products are wired through the shared
:class:`~vivarium.dagger.backend.wiring.ArtifactRegistry`, so downstream steps get real
edges to its products, and each one reaches the command as its absolute path through a
named ``{placeholder}``. With an ``env``,
:func:`~vivarium.dagger.backend.build.run_command` puts that environment's ``bin``
directories first on ``PATH`` for the whole command.

The command, with every path in it, is hashed with the step, so a changed command marks
the step stale. Code identity defaults to always-stale, as for
:func:`~vivarium.dagger.frontend.python_step.python_step`, so in this cut a command step
re-runs on every build.
"""

import difflib
import keyword
import shlex
import string
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pytask import PathNode

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactKey, ArtifactRegistry, WiringError
from vivarium.dagger.frontend.notebook_step import _RESERVED_NAMES


def command_step(
    command: str,
    *,
    produces: Mapping[str, ArtifactKey],
    registry: ArtifactRegistry,
    inputs: Mapping[str, ArtifactKey] | None = None,
    env: str | None = None,
    resources: dict[str, Any] | None = None,
    name: str | None = None,
) -> Task:
    """Compile the shell ``command`` into one task wired through ``registry``.

    Each ``{name}`` placeholder in ``command`` is replaced by the absolute path of the
    input or product of that name, quoted for the shell. Do not put quotes around a
    placeholder. Write ``{{`` and ``}}`` for a literal brace, as with
    :meth:`str.format`: a shell variable ``${VAR}`` is written ``${{VAR}}``, and the
    ``awk`` program ``'{print $1}'`` is written ``'{{print $1}}'``. The command does not
    have to use every input and product name.

    The command runs under ``/bin/sh`` (POSIX sh, which is dash on Ubuntu), not bash,
    both locally and on a compute node. Bash syntax such as ``[[ ]]``, ``source``,
    ``set -o pipefail`` or ``{a,b}`` brace expansion fails there, and the status of a
    pipe ``a | b`` is the status of ``b`` alone. To use bash, write ``bash -c '...'`` or
    put the work in a script. The command runs in the working directory of the
    ``dagger`` process.

    Before the command runs, the parent directory of every product is created; if that
    fails, the command does not run. Products that an earlier run left are not removed,
    so the command must write every product on every run: a product left by an earlier
    run counts as written.

    Parameters
    ----------
    command
        The shell command, with a ``{name}`` placeholder for each input or product path
        it uses.
    produces
        Maps placeholder names to the artifact keys of the files the command writes. Each
        key is registered with ``registry`` as a product of the step. It must have at
        least one entry, and each key must resolve to its own path.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    inputs
        Maps placeholder names to the artifact keys the registry resolves to the producing
        step's node (or a declared root input). An input that the command does not name
        still orders the step after its producer and marks the step stale when its file
        changes. To make an edit to a script that the command runs mark the step stale,
        declare the script as a root input and pass it here.
    env
        The environment whose ``bin`` directories go first on ``PATH`` while the command
        runs: a conda env or venv name, or a path to its prefix. Resolved when the
        pipeline is built or previewed, not here. ``None`` runs the command with the
        ``PATH`` of the process that runs it. For a local run that is the ``dagger``
        process's ``PATH`` as it is. For a run through Jobmon it is the compute node's
        ``PATH`` with the ``bin`` directory of the environment ``dagger`` runs in put
        first. Name the environment when the command needs a tool from a particular one,
        so that both kinds of run find the same tool.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster bridge to
        read later.
    name
        The step's name, which becomes the task name. Defaults to the ``produces`` key
        when ``produces`` has one entry, and is required when it has more than one.

    Returns
    -------
        One :class:`~vivarium.dagger.backend.task.Task` whose ``run`` is the command with
        its placeholders replaced, preceded by the creation of the product directories;
        whose ``env`` is ``env``; whose inputs are the resolved input nodes (the
        producing step's own node for a produced key, or a node at the file's path for a
        root input) and whose outputs are the registry's nodes for the ``produces`` keys,
        each keyed by its placeholder name; and whose resources are ``resources``, or
        ``{}`` when it is ``None``. Equal arguments give an identical command, whatever
        the order of the mappings.

    Raises
    ------
    vivarium.dagger.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or a ``produces`` key
        is a declared root input or resolves to the path of a different key that is
        already registered (another step's product or a root input). The message starts
        with ``Step '<name>':``, followed by the registry's message, which names the
        artifact key. The same key claimed by two steps is left for the build to reject.
    ValueError
        If ``command`` is empty or only whitespace; ``name`` or ``env`` is given and
        empty; ``produces`` is empty; ``name`` is ``None`` and ``produces`` has more than
        one entry; a name in ``inputs`` or ``produces`` is not a valid Python identifier,
        is a Python keyword, is reserved (``__code_id__``, ``__command__``,
        ``__executed__``, ``__notebook__``) or appears in both; two ``produces`` keys
        resolve to one path (the message names both); an input and a product are one
        artifact, by key or by resolved path (the message names both, with their keys);
        a placeholder in ``command`` is not exactly an input or product name, as for an
        unknown name, ``{}``, ``{0}``, a conversion, a format spec, an attribute or an
        index; or ``command`` has a single ``{`` or ``}`` that is not part of a
        placeholder. A placeholder message quotes the placeholder as written, lists the
        input and product names in sorted order, suggests the closest name when one is
        close, and says how to write a literal brace. The message for a single brace
        says that the command has a single ``{`` or ``}`` that is not part of a
        placeholder, lists the names and says how to write a literal brace. A ``{``
        that does not open a placeholder counts as a single brace even when a
        placeholder follows it, as in ``sh { {script}``. A placeholder that cannot be
        parsed, such as ``{out!}`` or ``{out!rr}``, is not a single brace; its message
        lists the names and says how to write a literal brace. Each message starts with
        ``Step '<name>':``, except that an empty ``name``, an empty ``produces`` with no
        ``name``, and more than one product with no ``name`` start with
        ``command_step:``. Every one of these is raised before anything is registered.
    """
    task_name = _step_name(produces, name)
    if not command.strip():
        raise ValueError(
            f"Step {task_name!r}: command must not be empty or only whitespace, "
            f"got {command!r}."
        )
    if env is not None and not env:
        raise ValueError(
            f"Step {task_name!r}: env must be a non-empty string or None, got {env!r}."
        )
    if not produces:
        raise ValueError(_no_product(f"Step {task_name!r}"))
    input_keys: dict[str, ArtifactKey] = dict(inputs or {})
    product_keys: dict[str, ArtifactKey] = dict(produces)
    _check_names(task_name, input_keys, product_keys)
    segments = _parse_command(task_name, command, sorted({*input_keys, *product_keys}))
    _check_shared_keys(task_name, input_keys, product_keys)

    try:
        dependencies = {
            param: registry.resolve_input(key) for param, key in input_keys.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    product_paths = {param: registry.path_for(key) for param, key in product_keys.items()}
    _check_paths(task_name, input_keys, dependencies, product_keys, product_paths)
    try:
        products = {
            param: registry.register_output(key) for param, key in product_keys.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None

    paths = {param: Path(str(node.path)) for param, node in dependencies.items()}
    paths.update(product_paths)
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=_shell_command(segments, paths, product_paths.values()),
        inputs=dependencies,
        outputs=products,
        resources={} if resources is None else resources,
        env=env,
    )


def _no_product(prefix: str) -> str:
    """Return the message for a step with no product, starting with ``prefix``."""
    return (
        f"{prefix}: produces must have at least one entry; a step with no product cannot "
        "be consumed and cannot be skipped."
    )


def _step_name(produces: Mapping[str, ArtifactKey], name: str | None) -> str:
    """Return the step's name; raise the errors that have no name to report."""
    if name is not None:
        if not name:
            raise ValueError(f"command_step: name must be a non-empty string, got {name!r}.")
        return name
    if not produces:
        raise ValueError(_no_product("command_step"))
    if len(produces) > 1:
        raise ValueError(
            "command_step: produces has more than one entry, with keys "
            f"{_quoted(sorted(produces.values()))}, so the step has no default name; "
            "pass name= to name it."
        )
    return next(iter(produces.values()))


def _quoted(names: Sequence[str]) -> str:
    """Return ``names`` quoted and joined as ``'a', 'b' and 'c'``."""
    quoted = [repr(each) for each in names]
    if len(quoted) < 2:
        return "".join(quoted)
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _check_names(
    task_name: str, inputs: Mapping[str, ArtifactKey], produces: Mapping[str, ArtifactKey]
) -> None:
    """Reject a name that is not an identifier, is a keyword or reserved, or is used twice."""
    for group, names in (("inputs", inputs), ("produces", produces)):
        for name in names:
            if not name.isidentifier():
                problem = "is not a valid Python identifier"
            elif keyword.iskeyword(name):
                problem = "is a Python keyword"
            elif name in _RESERVED_NAMES:
                reserved = ", ".join(repr(each) for each in sorted(_RESERVED_NAMES))
                problem = f"is reserved; the reserved names are {reserved}"
            elif group == "produces" and name in inputs:
                problem = (
                    "also appears in inputs; a name may appear in only one of inputs "
                    "and produces"
                )
            else:
                continue
            raise ValueError(f"Step {task_name!r}: name {name!r} in {group} {problem}.")


def _names_and_hint(names: Sequence[str], suggestion: str = "") -> str:
    """List the valid placeholder names, then add ``suggestion`` and the brace hint."""
    noun = "name is" if len(names) == 1 else "names are"
    return (
        f"The {noun} {_quoted(names)}.{suggestion} To write a literal brace in the "
        "command, double it: {{ or }}."
    )


def _parse_command(
    task_name: str, command: str, names: Sequence[str]
) -> list[tuple[str, str | None]]:
    """Split ``command`` into pairs of literal text and the placeholder name after it."""
    try:
        parsed = list(string.Formatter().parse(command))
    except ValueError as error:
        # Formatter.parse reports a lone or unclosed brace, or a { inside a field
        # name (as in "sh { {script}"), with these messages; its other errors come
        # from a malformed conversion or an unclosed format spec, such as {out!rr}
        # or {out:.
        single_brace = (
            "Single '",
            "expected '}' before end of string",
            "unexpected '{' in field name",
        )
        if str(error).startswith(single_brace):
            problem = "a single { or } that is not part of a placeholder"
        else:
            problem = "a { or } that does not form a placeholder"
        raise ValueError(
            f"Step {task_name!r}: the command has {problem}. {_names_and_hint(names)}"
        ) from None
    segments: list[tuple[str, str | None]] = []
    position = 0
    for literal, field, spec, conversion in parsed:
        # Every brace in the literal text was written doubled in the command.
        position += len(literal) + literal.count("{") + literal.count("}")
        if field is None:
            segments.append((literal, None))
            continue
        written = _placeholder_text(field, spec, conversion)
        if not command.startswith(written, position):
            # Formatter.parse gives the same empty spec for {x} and {x:}.
            written = f"{written[:-1]}:}}"
        position += len(written)
        if written != f"{{{field}}}" or field not in names:
            raise ValueError(_placeholder_error(task_name, written, field, names))
        segments.append((literal, field))
    return segments


def _placeholder_text(field: str, spec: str | None, conversion: str | None) -> str:
    """Return a placeholder as written, from the parts Formatter.parse gives."""
    converted = "" if conversion is None else f"!{conversion}"
    formatted = f":{spec}" if spec else ""
    return f"{{{field}{converted}{formatted}}}"


def _placeholder_error(step: str, written: str, field: str, names: Sequence[str]) -> str:
    """Return the message for a placeholder that is not exactly a valid name."""
    if field.isidentifier() and written == f"{{{field}}}":
        problem = f"names {written}, which is not an input or product of the step"
    else:
        problem = (
            f"has the placeholder {written}, but a placeholder must be exactly the name of "
            "an input or product, with no conversion, format spec, attribute or index"
        )
    close = difflib.get_close_matches(field.split(".")[0].split("[")[0], names, n=1)
    suggestion = f" Did you mean {close[0]!r}?" if close else ""
    return f"Step {step!r}: the command {problem}. {_names_and_hint(names, suggestion)}"


def _check_shared_keys(
    task_name: str, inputs: Mapping[str, ArtifactKey], produces: Mapping[str, ArtifactKey]
) -> None:
    """Reject an input and a product that name the same artifact key."""
    pairs = [
        f"input {name!r} (key {key!r}) and product {product!r} (key {product_key!r}) are "
        "one artifact"
        for name, key in inputs.items()
        for product, product_key in produces.items()
        if key == product_key
    ]
    if pairs:
        raise ValueError(_one_artifact_message(task_name, pairs))


def _check_paths(
    task_name: str,
    inputs: Mapping[str, ArtifactKey],
    dependencies: Mapping[str, PathNode],
    produces: Mapping[str, ArtifactKey],
    product_paths: Mapping[str, Path],
) -> None:
    """Reject two products at one path, or an input at a product's path."""
    claimed: dict[Path, str] = {}
    for product, path in product_paths.items():
        label = f"product {product!r} (key {produces[product]!r})"
        if path in claimed:
            raise ValueError(
                f"Step {task_name!r}: {claimed[path]} and {label} resolve to one path "
                f"{str(path)!r}; each product must be written to its own path."
            )
        claimed[path] = label
    pairs: list[str] = []
    for name, node in dependencies.items():
        path = Path(str(node.path)).resolve()
        if path in claimed:
            pairs.append(
                f"input {name!r} (key {inputs[name]!r}) and {claimed[path]} are one "
                f"artifact at {str(path)!r}"
            )
    if pairs:
        raise ValueError(_one_artifact_message(task_name, pairs))


def _one_artifact_message(task_name: str, pairs: Iterable[str]) -> str:
    """Return the message for inputs and products that are one artifact."""
    return (
        f"Step {task_name!r}: {'; '.join(pairs)}; a step cannot read and write the same "
        "artifact."
    )


def _shell_command(
    segments: Iterable[tuple[str, str | None]],
    paths: Mapping[str, Path],
    products: Iterable[Path],
) -> str:
    """Return the command that makes the product directories, then runs the author's."""
    body = "".join(
        literal + ("" if field is None else shlex.quote(str(paths[field])))
        for literal, field in segments
    )
    directories = sorted({str(path.parent) for path in products})
    # The author's text follows on its own line, unwrapped, so exit, cd, a trailing
    # comment and a heredoc behave as written.
    return f"mkdir -p {shlex.join(directories)} || exit $?\n{body}"
