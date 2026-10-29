"""Tests for the bridge's JSON codec and step recipes (``bridge.payload``)."""

import dataclasses
import functools
import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
from pytask import PNode, PythonNode

from vivarium.workflow.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.workflow.backend.env import resolve_env_bin_path
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.task import Task
from vivarium.workflow.bridge.payload import (
    PATH_TAG,
    Payload,
    PythonPayload,
    decode,
    encode,
    payload_for,
)
from vivarium.workflow.frontend.python_step import PythonStepRun
from vivarium.workflow.harness import _double, build_fixture


@dataclasses.dataclass
class _Point:
    """A small dataclass, which the codec does not encode."""

    x: int
    y: int


class _Opaque:
    """An arbitrary object, which the codec does not encode."""


_WRAPPERS: dict[str, Callable[[object], object]] = {
    "bare": lambda value: value,
    "in_list": lambda value: [1, value],
    "in_dict": lambda value: {"outer": {"inner": [value]}},
}
"""Ways to place a value that cannot be encoded, at the top level or nested."""


def _constant() -> pd.DataFrame:
    """Return a fixed frame."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _in_main() -> pd.DataFrame:
    """Stand in for a step function defined in a script or a notebook."""
    return _constant()


setattr(_in_main, "__module__", "__main__")


def _make_inner() -> Callable[[], pd.DataFrame]:
    """Return a step function defined inside this one."""

    def inner() -> pd.DataFrame:
        return _constant()

    return inner


def _hand_built(**kwargs: Any) -> None:
    """Stand in for a task run written by hand rather than by python_step."""


def _round_trip(value: object) -> Any:
    """Encode ``value``, pass it through JSON text, and decode it."""
    return decode(json.loads(json.dumps(encode(value))))


def _as_python(payload: Payload) -> PythonPayload:
    """Return ``payload`` after checking that it is a python recipe."""
    python = cast(PythonPayload, payload)
    assert python["kind"] == "python", payload
    return python


def _without_identity(message: str, target: Callable[..., Any]) -> str:
    """Remove from ``message`` every string that names ``target`` itself."""
    names = {repr(target), str(target)}
    qualname = getattr(target, "__qualname__", None)
    if qualname is not None:
        module = getattr(target, "__module__", None)
        names |= {qualname, f"{module}:{qualname}", f"{module}.{qualname}"}
    for name in sorted(names, key=len, reverse=True):
        message = message.replace(name, "")
    return message


def _make_prefix(prefix: Path) -> Path:
    """Create an env prefix holding an empty ``bin/python`` and return it."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").touch()
    return prefix


def _command_task(name: str, run: str, env: str | None, product: Path) -> Task:
    """Build a command task that produces ``product`` under ``env``."""
    return Task(
        name=name,
        run=run,
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=env,
    )


def _python_task(
    name: str,
    target: Callable[..., Any],
    product: Path,
    inputs: Mapping[str, PNode] | None = None,
) -> Task:
    """Build a task whose run is a PythonStepRun of ``target`` saving to ``product``."""
    return Task(
        name=name,
        run=PythonStepRun(target, ".parquet"),
        inputs={} if inputs is None else inputs,
        outputs={"product": file_node(product)},
        resources={},
        env=None,
    )


def test_encode_tags_a_path_and_keeps_a_relative_path_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """encode(Path('/a')) is {PATH_TAG: '/a'}, and a relative path is written as given, not made absolute."""
    monkeypatch.chdir(tmp_path)

    assert encode(Path("/a")) == {PATH_TAG: "/a"}
    assert encode(Path("data/raw.parquet")) == {PATH_TAG: "data/raw.parquet"}


@pytest.mark.parametrize(
    "value",
    [
        "text",
        "",
        0,
        -7,
        2.5,
        True,
        False,
        None,
        Path("/abs/raw.parquet"),
        Path("rel/raw.parquet"),
        [1, "a", None, Path("/abs/x.csv"), [Path("rel/y.csv"), {"z": 2.5}]],
        {"paths": [Path("/abs/x.csv")], "inner": {"path": Path("rel/y.csv"), "n": None}},
    ],
    ids=[
        "str",
        "empty_str",
        "int",
        "negative_int",
        "float",
        "true",
        "false",
        "none",
        "absolute_path",
        "relative_path",
        "nested_list",
        "nested_dict",
    ],
)
def test_codec_round_trips_scalars_none_paths_and_nested_containers(value: object) -> None:
    """decode(json.loads(json.dumps(encode(x)))) == x for str, int, float, bool, None, a Path, and nested lists and dicts holding paths."""
    decoded = _round_trip(value)

    assert decoded == value
    assert type(decoded) is type(value)


def test_a_tuple_comes_back_as_a_list() -> None:
    """A tuple, including one nested in a dict, encodes and decodes as a list with the same items."""
    value = {"pair": (1, Path("/a")), "nested": [("x", ("y", None))]}
    expected = {"pair": [1, Path("/a")], "nested": [["x", ["y", None]]]}

    assert encode((1, "a")) == [1, "a"]
    assert _round_trip((1, Path("/a"))) == [1, Path("/a")]
    assert _round_trip(value) == expected
    assert decode(encode(value)) == expected


@pytest.mark.parametrize("wrap", ["bare", "in_list", "in_dict"])
@pytest.mark.parametrize(
    "value, type_name",
    [
        ({1, 2}, "set"),
        (_Point(1, 2), "_Point"),
        (np.array([1, 2]), "ndarray"),
        (_Opaque(), "_Opaque"),
    ],
    ids=["set", "dataclass", "numpy_array", "object"],
)
def test_encode_rejects_values_that_are_not_json_or_a_path(
    value: object, type_name: str, wrap: str
) -> None:
    """encode raises TypeError naming the type for a set, a dataclass instance, a NumPy array, and an arbitrary object, also when nested in a list or dict."""
    with pytest.raises(TypeError, match=rf"\b{re.escape(type_name)}\b"):
        encode(_WRAPPERS[wrap](value))


@pytest.mark.parametrize(
    "value", [{1: "a"}, {"outer": [{2: "b"}]}], ids=["top_level", "nested"]
)
def test_encode_rejects_a_dict_key_that_is_not_a_string(value: object) -> None:
    """encode raises TypeError for a dict with an integer key rather than turning the key into a string."""
    with pytest.raises(TypeError):
        encode(value)


@pytest.mark.parametrize(
    "value",
    [{PATH_TAG: "/a"}, {PATH_TAG: "/a", "n": 1}, [{"outer": {PATH_TAG: "/a"}}]],
    ids=["only_key", "one_of_two_keys", "nested"],
)
def test_encode_rejects_a_dict_keyed_by_the_path_tag(value: object) -> None:
    """encode raises TypeError for a dict that has PATH_TAG as a key, so it cannot later decode as a path."""
    with pytest.raises(TypeError, match=re.escape(PATH_TAG)):
        encode(value)


def test_payload_for_a_python_step_names_its_target_suffix_arguments_and_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """For the harness's first step, payload_for gives kind 'python', the task name, target 'vivarium.workflow.harness:_double', suffix '.parquet', kwargs equal to the encoded input and product paths, and cwd equal to os.getcwd(); json.dumps accepts it."""
    controller = tmp_path / "controller"
    controller.mkdir()
    monkeypatch.chdir(controller)
    fixture = build_fixture(tmp_path)
    task = fixture.tasks[0]

    payload = payload_for(task, pytask_kwargs(task))

    raw, product = fixture.inputs["raw"].resolve(), fixture.registry.path_for("mid.parquet")
    assert dict(payload) == {
        "kind": "python",
        "task": "mid.parquet",
        "cwd": os.getcwd(),
        "target": "vivarium.workflow.harness:_double",
        "suffix": ".parquet",
        "kwargs": {"raw": {PATH_TAG: str(raw)}, "product": {PATH_TAG: str(product)}},
    }
    assert json.loads(json.dumps(payload)) == dict(payload)


def test_payload_for_leaves_out_the_reserved_arguments(
    tmp_path: Path, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """The '__code_id__' and '__command__' entries of pytask's kwargs do not appear in a python payload's kwargs."""
    task = build_fixture(tmp_path).tasks[0]
    kwargs = {**pytask_kwargs(task), COMMAND_KEY: "echo not an argument"}

    payload = _as_python(payload_for(task, kwargs))

    assert CODE_ID_KEY in kwargs
    assert set(payload["kwargs"]) == {"raw", "product"}


def test_payload_for_a_command_step_without_env_has_no_bin_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A command-string task with env None gives kind 'command', the task name, the command unchanged, env_path None, and cwd equal to os.getcwd()."""
    monkeypatch.chdir(tmp_path)
    command = "papermill 'in put.ipynb' out.ipynb -p year 2024 && echo $HOME"
    task = _command_task("render", command, None, tmp_path / "out.ipynb")

    payload = payload_for(task, pytask_kwargs(task))

    assert dict(payload) == {
        "kind": "command",
        "task": "render",
        "cwd": os.getcwd(),
        "command": command,
        "env_path": None,
    }
    assert json.loads(json.dumps(payload)) == dict(payload)


@pytest.mark.parametrize("venv", [False, True], ids=["conda_prefix", "venv_prefix"])
def test_payload_for_a_command_step_carries_its_env_resolved_to_bin_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    venv: bool,
    probe_tool: str,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A command task whose env is a fake prefix path (holding bin/python) gives env_path equal to resolve_env_bin_path of that prefix."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    prefix = _make_prefix(tmp_path / "env")
    if venv:
        base_bin = _make_prefix(tmp_path / "base") / "bin"
        (prefix / "pyvenv.cfg").write_text(f"home = {base_bin}\n")
    task = _command_task("tool_step", f"{probe_tool} run", str(prefix), tmp_path / "out.txt")

    payload = payload_for(task, pytask_kwargs(task))

    bin_path = resolve_env_bin_path(str(prefix.resolve()))
    assert (":" in bin_path) is venv
    assert dict(payload) == {
        "kind": "command",
        "task": "tool_step",
        "cwd": os.getcwd(),
        "command": f"{probe_tool} run",
        "env_path": bin_path,
    }


def test_payload_for_resolves_an_env_without_bin_python_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_tool: str,
    fake_conda: Callable[..., Path],
    make_tool_dir: Callable[[Path], Path],
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A command task naming an env that a fake conda lists, whose prefix has bin/<tool> but no bin/python, gives env_path equal to that prefix's bin directory."""
    monkeypatch.chdir(tmp_path)
    prefix = tmp_path / "conda_envs" / "r_only"
    make_tool_dir(prefix / "bin")
    fake_conda(prefix)
    task = _command_task("r_step", f"{probe_tool} report", "r_only", tmp_path / "report.html")

    payload = payload_for(task, pytask_kwargs(task))

    assert not (prefix / "bin" / "python").exists()
    assert dict(payload)["env_path"] == f"{prefix.resolve()}/bin"


@pytest.mark.parametrize("given_as", ["path", "name"])
def test_payload_for_refuses_an_env_that_does_not_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    given_as: str,
    fake_conda: Callable[..., Path],
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A command task whose env does not resolve makes payload_for raise ValueError naming the step and the env."""
    monkeypatch.chdir(tmp_path)
    if given_as == "path":
        monkeypatch.delenv("CONDA_EXE", raising=False)
        env = str(tmp_path / "no_such_env_dir")
    else:
        fake_conda(_make_prefix(tmp_path / "conda_envs" / "other"))
        env = "no_such_env_xyz_vw"
    task = _command_task("render_report", "true", env, tmp_path / "report.html")

    with pytest.raises(ValueError) as info:
        payload_for(task, pytask_kwargs(task))

    assert "render_report" in str(info.value)
    assert env in str(info.value)


def test_payload_for_refuses_a_hand_built_callable_run(
    tmp_path: Path, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """A task whose run is a plain function (not a PythonStepRun) makes payload_for raise ValueError naming the step."""
    task = Task(
        name="hand_built_step",
        run=_hand_built,
        inputs={},
        outputs={"product": file_node(tmp_path / "out.parquet")},
        resources={},
        env=None,
    )

    with pytest.raises(ValueError, match="hand_built_step"):
        payload_for(task, pytask_kwargs(task))


@pytest.mark.parametrize(
    "target, reason",
    [
        pytest.param(_in_main, "__main__|script|notebook", id="main"),
        pytest.param(lambda: _constant(), "lambda", id="lambda"),
        pytest.param(_make_inner(), "nested|inside another function", id="nested"),
        pytest.param(functools.partial(_double, Path("x")), "partial", id="partial"),
    ],
)
def test_payload_for_refuses_targets_that_cannot_be_imported_by_reference(
    tmp_path: Path,
    target: Callable[..., Any],
    reason: str,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A PythonStepRun whose target is in __main__, a lambda, a nested function, or a functools.partial makes payload_for raise ValueError naming the step and the reason."""
    task = _python_task("bridged_step", target, tmp_path / "out.parquet")

    with pytest.raises(ValueError) as info:
        payload_for(task, pytask_kwargs(task))

    message = str(info.value)
    assert "bridged_step" in message
    assert re.search(reason, _without_identity(message, target)), message


@pytest.mark.parametrize("value", [{1, 2}, object()], ids=["set", "object"])
def test_payload_for_refuses_an_argument_that_cannot_be_encoded(
    tmp_path: Path, value: object, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """A python step whose kwargs hold a value encode refuses (a set, or an object) makes payload_for raise ValueError naming the step and the argument."""
    inputs = {"lookup_table": PythonNode(name="lookup_table", value=value)}
    task = _python_task("bridged_step", _double, tmp_path / "out.parquet", inputs)

    with pytest.raises(ValueError) as info:
        payload_for(task, pytask_kwargs(task))

    assert "bridged_step" in str(info.value)
    assert "lookup_table" in str(info.value)


def test_payload_for_resolves_a_named_env_once_across_calls_sharing_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_tool: str,
    fake_conda: Callable[..., Path],
    make_tool_dir: Callable[[Path], Path],
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """Two command tasks naming the same fake-conda env, given one shared env_paths dict, run the conda lookup once, both get the same env_path, and env_paths then maps the env name to it."""
    monkeypatch.chdir(tmp_path)
    prefix = tmp_path / "conda_envs" / "shared_env"
    make_tool_dir(prefix / "bin")
    calls = fake_conda(prefix)
    tasks = [
        _command_task(name, f"{probe_tool} {name}", "shared_env", tmp_path / f"{name}.txt")
        for name in ("first_step", "second_step")
    ]
    env_paths: dict[str, str] = {}

    recipes = [payload_for(task, pytask_kwargs(task), env_paths=env_paths) for task in tasks]

    bin_path = f"{prefix.resolve()}/bin"
    assert len(calls.read_text().splitlines()) == 1
    assert [dict(recipe)["env_path"] for recipe in recipes] == [bin_path, bin_path]
    assert env_paths == {"shared_env": bin_path}


def test_payload_for_uses_a_bin_path_already_in_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_conda: Callable[..., Path],
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """A command task whose env is already a key of env_paths gets that value as its env_path without any lookup, even when the env would not resolve."""
    monkeypatch.chdir(tmp_path)
    calls = fake_conda()
    cached = "/cached/env/bin:/cached/base/bin"
    task = _command_task("cached_step", "true", "unlisted_env", tmp_path / "out.txt")
    env_paths = {"unlisted_env": cached}

    payload = payload_for(task, pytask_kwargs(task), env_paths=env_paths)

    assert dict(payload)["env_path"] == cached
    assert calls.read_text() == ""
    assert env_paths == {"unlisted_env": cached}
