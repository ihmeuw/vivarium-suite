"""Call a python step's target and save the DataFrames it returns.

A :class:`PythonStepRun` holds a step's target and its output suffix or suffixes, and
:func:`resolve_reference` imports a target from its import reference, so a process that
has only the reference and the suffixes can rebuild the run and call it.
"""

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

PYTHON_OUTPUT = "__output__"
"""Task output name under which a python step with one output gets its output's path."""


def _save_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Write ``frame`` to ``path`` as Parquet."""
    frame.to_parquet(path)


def _save_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write ``frame`` to ``path`` as CSV without the index."""
    frame.to_csv(path, index=False)


_SAVERS: dict[str, Callable[[pd.DataFrame, Path], None]] = {
    ".parquet": _save_parquet,
    ".csv": _save_csv,
}
"""Supported output suffixes mapped to the DataFrame save they dispatch to."""


def _check_suffix(suffix: str, subject: str) -> None:
    """Raise ValueError naming ``subject`` if ``suffix`` has no save format."""
    if suffix not in _SAVERS:
        raise ValueError(
            f"{subject} has unsupported suffix {suffix!r}; supported suffixes are "
            f"{', '.join(sorted(_SAVERS))}."
        )


_MISSING_RETURN = " Check for a missing return statement."
"""The hint added to the message for a target that returned ``None``."""


@dataclass(frozen=True)
class PythonStepRun:
    """Call a step's target with its loaded inputs and save the frames it returns.

    The target returns one DataFrame, or a dict of them by output name. This is the
    ``run`` of every task :func:`~vivarium.dagger.frontend.python_step.python_step`
    builds. It holds only the target and the output suffix, or each output name's suffix,
    so another process can rebuild an equal run from
    :func:`~vivarium.dagger.frontend.python_step.target_reference` of the target and the
    suffixes.
    """

    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]]
    """The step's function. Any callable runs locally; running the step in another
    process needs :func:`~vivarium.dagger.frontend.python_step.target_reference` to
    succeed for it."""
    suffix: str | Mapping[str, str]
    """The output key's suffix, which names the save format (``.parquet`` or ``.csv``),
    for a step with one output under ``"__output__"``; or, for a step whose ``outputs`` is
    a mapping, each output name mapped to its key's suffix."""

    def __post_init__(self) -> None:
        """Reject a suffix that has no save format, or an empty mapping; copy a mapping."""
        if isinstance(self.suffix, str):
            _check_suffix(self.suffix, "Output")
            return
        if not self.suffix:
            raise ValueError(
                "A python step run needs at least one output suffix, got an empty mapping."
            )
        for name, suffix in self.suffix.items():
            _check_suffix(suffix, f"Output {name!r}")
        # A copy, so that a later change to the caller's mapping cannot change the run.
        object.__setattr__(self, "suffix", dict(self.suffix))

    def __hash__(self) -> int:
        """Hash the target and the suffix, or the mapping's entries in any order."""
        if isinstance(self.suffix, str):
            return hash((self.target, self.suffix))
        return hash((self.target, frozenset(self.suffix.items())))

    def __call__(self, **kwargs: Any) -> None:
        """Call ``target`` and save what it returns at each output's path.

        With one output, ``kwargs["__output__"]`` is the output's path, and ``target``
        returns the DataFrame to save there. With a mapping of suffixes, each output name
        in it is a keyword argument that holds that output's path, and ``target`` returns
        a dict that maps each output name to its DataFrame. The type of the result, its
        names and each of its values are checked before anything is saved, and the frames
        are then saved in the mapping's order.

        Each output's parent directory is created before ``target`` is called. Every
        other keyword argument is passed to ``target`` unchanged.

        Raises
        ------
        TypeError
            With one output, if ``target`` returns something other than a DataFrame;
            with a mapping, if it returns something other than a dict, or a dict with a
            value that is not a DataFrame. The message names ``target`` and the type it
            returned, or each output name whose value has the wrong type and that
            value's type. For ``None`` it suggests a missing ``return``.
        ValueError
            With a mapping, if the keys of the returned dict are not exactly the output
            names. The message names ``target``, each missing output name, each extra
            key and the output names.
        """
        __tracebackhide__ = True
        if isinstance(self.suffix, str):
            path = Path(kwargs.pop(PYTHON_OUTPUT))
            path.parent.mkdir(parents=True, exist_ok=True)
            result: object = self.target(**kwargs)
            if not isinstance(result, pd.DataFrame):
                if result is None:
                    hint = _MISSING_RETURN
                elif isinstance(result, Mapping):
                    hint = (
                        " To write several outputs, pass outputs= a mapping of output "
                        "names to file keys."
                    )
                else:
                    hint = ""
                raise TypeError(
                    f"Python step target {_label(self.target)} returned "
                    f"{type(result).__name__}; a python step must return a pandas "
                    f"DataFrame.{hint}"
                )
            _SAVERS[self.suffix](result, path)
            return
        paths = {name: Path(kwargs.pop(name)) for name in self.suffix}
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        returned: object = self.target(**kwargs)
        frames = _check_result(_label(self.target), returned, list(self.suffix))
        for name, suffix in self.suffix.items():
            _SAVERS[suffix](frames[name], paths[name])


def _label(target: Callable[..., Any]) -> str:
    """Return ``target``'s qualified name, or its repr when it has none."""
    qualname = getattr(target, "__qualname__", None)
    return qualname if isinstance(qualname, str) else repr(target)


def _joined(parts: Sequence[str]) -> str:
    """Return ``parts`` joined as ``a, b and c``."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _output_names(names: Sequence[str]) -> str:
    """Return the sentence that lists the output names, sorted."""
    noun = "name is" if len(names) == 1 else "names are"
    return f"The output {noun} {_joined([repr(name) for name in sorted(names)])}."


def _check_result(
    label: str, result: object, names: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """Return ``result`` as frames by output name; raise if it does not match ``names``."""
    __tracebackhide__ = True
    if not isinstance(result, Mapping):
        hint = _MISSING_RETURN if result is None else ""
        raise TypeError(
            f"Python step target {label} returned {type(result).__name__}; a python step "
            "with a mapping of outputs must return a dict that maps each output name to a "
            f"pandas DataFrame. {_output_names(names)}{hint}"
        )
    missing = sorted(set(names) - set(result))
    extra = sorted((key for key in result if key not in names), key=repr)
    parts: list[str] = []
    if missing:
        quoted = _joined([repr(name) for name in missing])
        verb = "output {} is" if len(missing) == 1 else "outputs {} are"
        parts.append(f"the {verb.format(quoted)} missing")
    if extra:
        quoted = _joined([repr(key) for key in extra])
        if len(extra) == 1:
            parts.append(f"the key {quoted} is not an output name")
        else:
            parts.append(f"the keys {quoted} are not output names")
    if parts:
        raise ValueError(
            f"Python step target {label} returned a dict whose keys do not match its "
            f"output names: {'; '.join(parts)}. {_output_names(names)}"
        )
    wrong = [
        f"for {name!r} is {type(result[name]).__name__}"
        for name in names
        if not isinstance(result[name], pd.DataFrame)
    ]
    if wrong:
        raise TypeError(
            f"Python step target {label} returned a dict whose value {_joined(wrong)}; "
            "each value must be a pandas DataFrame."
        )
    return {name: result[name] for name in names}


def resolve_reference(reference: str) -> Callable[..., Any]:
    """Import and return the callable that ``reference`` names.

    Parameters
    ----------
    reference
        ``"package.module:qualname"``, as
        :func:`~vivarium.dagger.frontend.python_step.target_reference` returns.

    Returns
    -------
        The named callable.

    Raises
    ------
    ValueError
        If ``reference`` is not of the form ``"module:qualname"``.
    ImportError
        If the module cannot be imported.
    AttributeError
        If the module has no object at the qualified name.
    """
    parts = reference.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Reference {reference!r} is not of the form 'module:qualname'.")
    module_name, qualname = parts
    found: Any = importlib.import_module(module_name)
    for attribute in qualname.split("."):
        found = getattr(found, attribute)
    return cast(Callable[..., Any], found)
