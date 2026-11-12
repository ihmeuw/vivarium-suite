"""Tests of ``simulation_step``: the task it returns, its command and options, its
definition-time errors, and runs through ``run``, ``preview`` and the ``dagger`` command
with a stub ``psimulate`` (see ``tests.frontend.simulations``)."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import traceback
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from click.testing import CliRunner, Result
from pytask import PathNode, TaskOutcome

from tests.frontend.notebooks import write_source_notebook
from tests.frontend.simulations import (
    COMMAND_FRAGMENTS,
    EXIT_VARIABLE,
    KNOB_VARIABLES,
    LOG_VARIABLE,
    METRIC,
    NO_PREVIOUS,
    RUN_DIRECTORIES_VARIABLE,
    UNFINISHED_VARIABLE,
    assert_no_command,
    copy_simulation_example,
    deaths_by_draw,
    make_stub_env,
    read_argv_log,
    scratch_for,
    snapshot,
    summarize_run,
    summary_values,
    write_branches,
    write_spec,
)
from vivarium.dagger import frontend
from vivarium.dagger.backend.build import (
    COLLECT_SIMULATION_SCRIPT,
    COLLECT_SIMULATION_VARIABLE,
)
from vivarium.dagger.backend.nodes import AlwaysStaleNode, RunDirectoryNode, file_node
from vivarium.dagger.backend.preview import Reason, StepPreview
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry, WiringError
from vivarium.dagger.cli import dagger
from vivarium.dagger.frontend import (
    Pipeline,
    PipelineError,
    StepFailed,
    _collect_simulation,
    notebook_step,
    preview,
    python_step,
    run,
    simulation_step,
)
from vivarium.dagger.frontend._collect_simulation import _MESSAGE_FILE, _SENTINEL
from vivarium.dagger.frontend.simulation_step import describe_simulation_failure
from vivarium.dagger.harness import Perturbed, with_stable_code_id

_KEY = "sims/toy"
"""The simulation step's produces key, which is also its name unless one is given."""

_CONSUMER = "summary"
"""The name of the python step that reads the run directory."""

_SUMMARY_KEY = "summary.parquet"
"""The consumer's product key."""

_NAMED_ENV = "simulation-env"
"""An env name for steps that are compiled but never built; an env is resolved at build."""

_OPTIONS: dict[str, str | int | float | bool] = {
    "project": "proj_simscience",
    "no_slack": True,
}
"""The options of most steps here."""

_ALLOWED = (
    "backup_freq",
    "hardware",
    "max_attempts",
    "max_runtime",
    "max_workers",
    "no_slack",
    "peak_memory",
    "project",
    "queue",
    "slack_channel",
    "slack_tag",
)
"""The options a step accepts, in sorted order."""


@dataclass(frozen=True)
class _Model:
    """A toy spec, a branches file of 4 simulations and a stub env, all under ``base``."""

    base: Path
    spec: Path
    branches: Path
    env: Path
    log: Path

    @property
    def state(self) -> Path:
        """Return the pipeline's root for pytask's state."""
        return self.base / "state"

    @property
    def run_dir(self) -> Path:
        """Return the run directory that ``_KEY`` resolves to."""
        return self.registry().path_for(_KEY)

    @property
    def scratch(self) -> Path:
        """Return the scratch directory psimulate writes to."""
        return scratch_for(self.run_dir)

    def registry(self, root_inputs: Mapping[str, Path] | None = None) -> ArtifactRegistry:
        """Return a new registry whose products go under ``base / "out"``."""
        return ArtifactRegistry(self.base / "out", "run1", root_inputs=root_inputs)

    def calls(self) -> list[list[str]]:
        """Return the arguments of each call of the stub psimulate, in call order."""
        return read_argv_log(self.log)

    def kept(self) -> str:
        """Return the sentence that says the previous complete run was kept."""
        return f"The previous complete run at {self.run_dir} was kept."


def _make_model(base: Path, monkeypatch: pytest.MonkeyPatch) -> _Model:
    """Write the toy inputs and a stub env under ``base``, and log the stub's calls there."""
    base.mkdir(parents=True, exist_ok=True)
    log = base / "psimulate_calls.jsonl"
    monkeypatch.setenv(LOG_VARIABLE, str(log))
    for variable in KNOB_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return _Model(
        base=base,
        spec=write_spec(base / "model" / "toy_model.yaml"),
        branches=write_branches(base / "model" / "branches.yaml"),
        env=make_stub_env(base / "env"),
        log=log,
    )


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Model:
    """Return a toy model and stub env under ``tmp_path``."""
    return _make_model(tmp_path.resolve(), monkeypatch)


def _step(model: _Model, registry: ArtifactRegistry, **overrides: Any) -> Task:
    """Return the simulation step of ``model``, with ``overrides`` of its arguments."""
    arguments: dict[str, Any] = {
        "spec": model.spec,
        "branches": model.branches,
        "produces": _KEY,
        "registry": registry,
        "env": str(model.env),
        "options": _OPTIONS,
    }
    arguments.update(overrides)
    return simulation_step(**arguments)


def _pipeline(
    model: _Model,
    *,
    stable: bool = False,
    consumer: bool = True,
    root_inputs: Mapping[str, Path] | None = None,
    **overrides: Any,
) -> Pipeline:
    """Return the simulation step and, with ``consumer``, a python step that reads its run.

    ``stable`` gives every step a stable code identity.
    """
    registry = model.registry(root_inputs)
    tasks = [_step(model, registry, **overrides)]
    if consumer:
        tasks.append(
            python_step(
                summarize_run,
                inputs={"simulation": _KEY},
                produces=_SUMMARY_KEY,
                registry=registry,
                name=_CONSUMER,
            )
        )
    if stable:
        tasks = [with_stable_code_id(task) for task in tasks]
    return Pipeline(tasks, model.state, registry)


def _constant() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _command(task: Task) -> str:
    """Return ``task``'s command string."""
    assert isinstance(task.run, str), task.run
    return task.run


def _parts(command: str) -> list[list[str]]:
    """Split ``command`` into the arguments of each part that '&&' joins."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    parts: list[list[str]] = [[]]
    for token in lexer:
        if token == "&&":
            parts.append([])
        else:
            parts[-1].append(token)
    return parts


def _part(task: Task, program: str) -> list[str]:
    """Return the arguments of the one part of ``task``'s command that runs ``program``."""
    [found] = [part for part in _parts(_command(task)) if part[:1] == [program]]
    return found


def _flags(task: Task) -> list[str]:
    """Return the arguments after ``-o <scratch>`` in the command's psimulate part."""
    argv = _part(task, "psimulate")
    return argv[argv.index("-o") + 2 :]


def _runs_in(scratch: Path) -> list[Path]:
    """Return the run directories psimulate left under ``scratch``, sorted."""
    return sorted(path for path in scratch.glob("*/*") if path.is_dir())


def _sentinel(model: _Model) -> bytes:
    """Return the bytes of the run directory's sentinel."""
    return (model.run_dir / _SENTINEL).read_bytes()


def _summary(model: _Model) -> pd.DataFrame:
    """Return the consumer's product."""
    return pd.read_parquet(model.registry().path_for(_SUMMARY_KEY))


def _step_failed(pipeline: Pipeline) -> StepFailed:
    """Run ``pipeline`` and return the StepFailed it raises."""
    with pytest.raises(StepFailed) as excinfo:
        run(pipeline)
    return excinfo.value


def _first_run(model: _Model, pipeline: Pipeline) -> dict[str, bytes]:
    """Run ``pipeline`` successfully and return the bytes of the run directory's files."""
    assert run(pipeline) == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}
    assert (model.run_dir / _SENTINEL).is_file()
    return snapshot(model.run_dir)


def _assert_no_run_in_scratch(model: _Model) -> None:
    """Assert that the scratch directory exists and holds no run directory."""
    assert model.scratch.is_dir()
    assert [path for path in model.scratch.iterdir() if path.is_dir()] == []


class _RemoteFailure(Exception):
    """Stand in for a failure reported from a Jobmon node, which names the node's error."""

    def __init__(self, exc_type: str) -> None:
        super().__init__("a step failed on a node")
        self.exc_type = exc_type
        self.exc_message = "Command 'psimulate run' returned non-zero exit status 1."


# The task


def test_simulation_step_returns_one_command_task_under_the_declared_env(
    model: _Model,
) -> None:
    """simulation_step returns a Task whose run is one command string, whose env is the given env, and whose code_id is always-stale."""
    task = _step(model, model.registry())
    named = _step(model, model.registry(), env=_NAMED_ENV)

    assert isinstance(task.run, str)
    assert task.env == str(model.env)
    assert isinstance(task.code_id, AlwaysStaleNode)
    assert named.env == _NAMED_ENV


def test_resources_are_carried_and_default_to_empty(model: _Model) -> None:
    """The task's resources are the given dict, or {} when resources is omitted."""
    resources = {"memory": 8, "runtime": "02:00:00", "queue": "all.q"}

    assert _step(model, model.registry(), resources=resources).resources == resources
    assert _step(model, model.registry()).resources == {}


def test_name_defaults_to_produces_and_can_be_overridden(model: _Model) -> None:
    """The task's name is produces when name is omitted, and name when given."""
    assert _step(model, model.registry()).name == _KEY
    assert _step(model, model.registry(), name="toy_simulation").name == "toy_simulation"


def test_inputs_are_the_spec_and_branches_file_nodes(model: _Model) -> None:
    """Without artifact, the task's inputs are exactly the file nodes of the resolved spec and branches paths, under '__spec__' and '__branches__'."""
    task = _step(model, model.registry())

    assert set(task.inputs) == {"__spec__", "__branches__"}
    expected = {"__spec__": model.spec.resolve(), "__branches__": model.branches.resolve()}
    for name, path in expected.items():
        node = task.inputs[name]
        assert isinstance(node, PathNode) and not isinstance(node, RunDirectoryNode), node
        assert node == file_node(path)
        assert node.path == path


def test_an_artifact_from_a_producing_step_is_an_input_with_the_producers_node(
    model: _Model,
) -> None:
    """With artifact naming another step's product, the task's '__artifact__' input is the identical node that step registered."""
    registry = model.registry()
    produced = registry.register_output("artifacts/model.hdf")

    task = _step(model, registry, artifact="artifacts/model.hdf")

    assert set(task.inputs) == {"__spec__", "__branches__", "__artifact__"}
    assert task.inputs["__artifact__"] is produced


def test_an_artifact_from_a_root_input_is_an_input_at_its_path(model: _Model) -> None:
    """With artifact naming a declared root input, the task's '__artifact__' input is a node at the root input's path."""
    artifact = model.base / "artifacts" / "model_artifact.hdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"an artifact")
    registry = model.registry({"artifact": artifact})

    task = _step(model, registry, artifact="artifact")

    node = task.inputs["__artifact__"]
    assert isinstance(node, PathNode)
    assert node.path == artifact.resolve()


def test_the_one_output_is_a_run_directory_node_at_the_produces_path(model: _Model) -> None:
    """The task has one output, '__run__', a RunDirectoryNode whose path is registry.path_for(produces), and resolve_input(produces) returns that node."""
    registry = model.registry()

    task = _step(model, registry)

    assert set(task.outputs) == {"__run__"}
    node = task.outputs["__run__"]
    assert isinstance(node, RunDirectoryNode)
    assert node.path == registry.path_for(_KEY)
    assert registry.resolve_input(_KEY) is node


def test_only_simulation_step_is_exported_from_the_frontend() -> None:
    """vivarium.dagger.frontend exports simulation_step, and no name in its __all__ refers to the sentinel."""
    assert "simulation_step" in frontend.__all__
    assert frontend.simulation_step is simulation_step
    for name in frontend.__all__:
        assert "sentinel" not in name.lower(), name
        assert "success" not in name.lower(), name


_IMPORT_CHECK = """
import sys

import vivarium.dagger.frontend.simulation_step

forbidden = ("jobmon.", "jobmon_", "vivarium.psimulate", "vivarium.engine")
for name in sorted(sys.modules):
    if name == "jobmon" or name.startswith(forbidden):
        print(name)
"""
"""A script that imports the simulation step and prints every forbidden module it loaded."""


def test_the_simulation_modules_import_neither_psimulate_nor_jobmon_nor_the_engine() -> None:
    """Importing vivarium.dagger.frontend.simulation_step in a fresh interpreter imports no vivarium.psimulate, vivarium.engine or jobmon module."""
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_CHECK], capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == []


# The command


def test_command_runs_psimulate_with_spec_branches_and_the_scratch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command runs 'psimulate run -M <spec> -B <branches> -o <run directory>.psimulate' with absolute, quoted paths and no '-i' when artifact is omitted."""
    model = _make_model(tmp_path.resolve() / "with a space", monkeypatch)

    argv = _part(_step(model, model.registry()), "psimulate")

    assert argv[:8] == [
        "psimulate",
        "run",
        "-M",
        str(model.spec),
        "-B",
        str(model.branches),
        "-o",
        str(model.scratch),
    ]
    assert model.scratch == model.run_dir.with_name("toy.psimulate")
    assert all(Path(path).is_absolute() for path in (argv[3], argv[5], argv[7]))
    assert "-i" not in argv


def test_command_passes_the_artifacts_path_as_i(model: _Model) -> None:
    """With artifact given, the command passes '-i' followed by the path of the artifact's resolved node."""
    artifact = model.base / "artifacts" / "model_artifact.hdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"an artifact")
    registry = model.registry({"artifact": artifact})

    argv = _part(_step(model, registry, artifact="artifact"), "psimulate")

    assert argv[:10] == [
        "psimulate",
        "run",
        "-M",
        str(model.spec),
        "-B",
        str(model.branches),
        "-i",
        str(artifact.resolve()),
        "-o",
        str(model.scratch),
    ]


def test_command_prepares_the_parent_and_a_fresh_scratch_directory_before_psimulate(
    model: _Model,
) -> None:
    """Before psimulate, the command creates the run directory's parent and removes and creates again the scratch directory, joined with '&&'."""
    parts = _parts(_command(_step(model, model.registry())))
    scratch, parent = str(model.scratch), str(model.run_dir.parent)

    [psimulate] = [index for index, part in enumerate(parts) if part[:1] == ["psimulate"]]
    before = parts[:psimulate]
    makes_parent = [
        index
        for index, part in enumerate(before)
        if part[:1] == ["mkdir"] and "-p" in part and parent in part
    ]
    removes = [
        index
        for index, part in enumerate(before)
        if part[:1] == ["rm"] and "-rf" in part and scratch in part
    ]
    makes_scratch = [
        index
        for index, part in enumerate(before)
        if part[:1] == ["mkdir"] and scratch in part
    ]
    assert makes_parent and removes and makes_scratch, parts
    assert removes[0] < makes_scratch[-1], parts


_CHECK_ARGUMENT = (
    f"${{{COLLECT_SIMULATION_VARIABLE}:?is set by dagger when it runs the step}}"
)
"""The check's script argument as the shell reads it, before it expands the variable."""


def _script_lines() -> list[str]:
    """Return the check script's lines that are long enough to identify it."""
    source = COLLECT_SIMULATION_SCRIPT.read_text(encoding="utf-8")
    return [line.strip() for line in source.splitlines() if len(line.strip()) > 30]


def test_command_runs_the_check_from_the_variable_with_isolated_bare_python(
    model: _Model,
) -> None:
    """The command's last part is 'python -I "${DAGGER_COLLECT_SIMULATION:?...}" <scratch> <run directory>', and the command holds neither sys.executable nor any other absolute path to an interpreter."""
    command = _command(_step(model, model.registry()))
    parts = _parts(command)

    assert parts[-1] == [
        "python",
        "-I",
        _CHECK_ARGUMENT,
        str(model.scratch),
        str(model.run_dir),
    ]
    assert parts[-2][:1] == ["psimulate"]
    assert sys.executable not in command
    assert str(Path(sys.executable).resolve()) not in command
    for part in parts:
        program = part[0]
        assert not (os.path.isabs(program) and "python" in Path(program).name), part


def test_command_holds_no_line_of_the_check_and_ends_with_its_hash(model: _Model) -> None:
    """The command contains no line of the check script and no path to it, and ends with the comment '# check' and the first 12 hex digits of the script's SHA-256."""
    command = _command(_step(model, model.registry()))
    digest = hashlib.sha256(COLLECT_SIMULATION_SCRIPT.read_bytes()).hexdigest()[:12]

    assert [line for line in _script_lines() if line in command] == []
    assert str(COLLECT_SIMULATION_SCRIPT) not in command
    assert command.endswith(f"  # check {digest}"), command[-80:]


def test_an_edited_check_changes_only_the_hash_at_the_end_of_the_command(
    model: _Model, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the check script replaced by a copy that has one more line, the step's command differs from the original's, and only in the hash after '# check'."""
    original = _command(_step(model, model.registry()))
    edited = tmp_path / "_collect_simulation.py"
    edited.write_text(COLLECT_SIMULATION_SCRIPT.read_text(encoding="utf-8") + "# edited\n")
    simulation_module = sys.modules["vivarium.dagger.frontend.simulation_step"]
    monkeypatch.setattr(simulation_module, "COLLECT_SIMULATION_SCRIPT", edited)

    changed = _command(_step(model, model.registry()))

    assert changed != original
    assert changed.rsplit("# check ", 1)[0] == original.rsplit("# check ", 1)[0]


def test_the_command_run_by_hand_without_the_variable_stops_naming_it(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command's check part, run with /bin/sh and no DAGGER_COLLECT_SIMULATION in the environment, exits non-zero with a message that names the variable and says dagger sets it, and writes no run directory."""
    monkeypatch.delenv(COLLECT_SIMULATION_VARIABLE, raising=False)
    check = _command(_step(model, model.registry())).split(" && ")[-1]

    result = subprocess.run(["/bin/sh", "-c", check], capture_output=True, text=True)

    assert result.returncode != 0
    assert COLLECT_SIMULATION_VARIABLE in result.stderr, result.stderr
    assert "is set by dagger when it runs the step" in result.stderr, result.stderr
    assert not model.run_dir.exists()


def test_command_paths_are_absolute_for_relative_spec_and_branches(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative spec and branches paths, given from a changed working directory, appear in the command as their resolved absolute paths."""
    monkeypatch.chdir(model.base)

    task = _step(
        model, model.registry(), spec="model/toy_model.yaml", branches="model/branches.yaml"
    )

    argv = _part(task, "psimulate")
    assert argv[argv.index("-M") + 1] == str(model.spec.resolve())
    assert argv[argv.index("-B") + 1] == str(model.branches.resolve())
    spec_node = task.inputs["__spec__"]
    assert isinstance(spec_node, PathNode)
    assert spec_node.path == model.spec.resolve()


def test_a_true_flag_is_bare_and_a_false_flag_is_left_out(model: _Model) -> None:
    """no_slack=True adds the bare '--no-slack', and no_slack=False adds nothing."""
    muted = _step(model, model.registry(), options={"project": "p", "no_slack": True})
    unmuted = _step(model, model.registry(), options={"project": "p", "no_slack": False})

    assert _flags(muted) == ["--no-slack", "--project", "p"]
    assert _flags(unmuted) == ["--project", "p"]


def test_numbers_are_passed_as_their_str(model: _Model) -> None:
    """An int option is passed as its str(), as in '--peak-memory 3', and a float as its str(), as in '--peak-memory 1.5'."""
    whole = _step(model, model.registry(), options={"project": "p", "peak_memory": 3})
    half = _step(model, model.registry(), options={"project": "p", "peak_memory": 1.5})

    assert _flags(whole) == ["--peak-memory", "3", "--project", "p"]
    assert _flags(half) == ["--peak-memory", "1.5", "--project", "p"]


def test_a_value_with_a_space_and_a_quote_is_quoted(model: _Model) -> None:
    """A str option with a space and a single quote is shell-quoted, so shlex.split of the command gives the value back unchanged."""
    value = 'the team\'s "queue" name'

    task = _step(model, model.registry(), options={"project": "p", "queue": value})

    assert _flags(task) == ["--project", "p", "--queue", value]
    assert value not in _command(task)


def test_option_flags_are_sorted_by_key_with_underscores_as_hyphens(model: _Model) -> None:
    """The option flags appear in key order, each as '--' followed by the key with '_' replaced by '-'."""
    options: dict[str, str | int | float | bool] = {
        "slack_tag": "someone",
        "slack_channel": "simulation-runs",
        "queue": "all.q",
        "project": "proj_simscience",
        "peak_memory": 2,
        "max_workers": 4,
        "max_runtime": "01:00:00",
        "max_attempts": 2,
        "hardware": "r650xs",
        "backup_freq": 30,
    }

    task = _step(model, model.registry(), options=options)

    assert _flags(task) == [
        "--backup-freq",
        "30",
        "--hardware",
        "r650xs",
        "--max-attempts",
        "2",
        "--max-runtime",
        "01:00:00",
        "--max-workers",
        "4",
        "--peak-memory",
        "2",
        "--project",
        "proj_simscience",
        "--queue",
        "all.q",
        "--slack-channel",
        "simulation-runs",
        "--slack-tag",
        "someone",
    ]


def test_command_is_identical_for_a_reordered_options_mapping(model: _Model) -> None:
    """Two steps with equal options given in different orders have identical commands."""
    options: dict[str, str | int | float | bool] = {
        "project": "proj_simscience",
        "queue": "all.q",
        "max_runtime": "01:00:00",
        "peak_memory": 2,
        "no_slack": True,
    }
    reordered = dict(reversed(list(options.items())))

    first = _step(model, model.registry(), options=options)
    second = _step(model, model.registry(), options=reordered)

    assert _command(first) == _command(second)


# Definition-time errors


def _definition_errors(model: _Model) -> dict[str, dict[str, Any]]:
    """Map each definition-time ValueError to the arguments that cause it."""
    missing = model.base / "model" / "missing.yaml"
    directory = model.base / "model"
    return {
        "empty-name": {"name": ""},
        "empty-env": {"env": ""},
        "empty-produces": {"produces": "", "name": "toy"},
        "produces-ends-in-a-slash": {"produces": "sims/toy/", "name": "toy"},
        "produces-is-the-output-directory": {"produces": ".", "name": "toy"},
        "produces-is-absolute": {"produces": str(model.base / "elsewhere"), "name": "toy"},
        "missing-spec": {"spec": missing},
        "spec-is-a-directory": {"spec": directory},
        "missing-branches": {"branches": missing},
        "branches-is-a-directory": {"branches": directory},
        "no-project": {"options": {"no_slack": True}},
        "step-owned-key": {"options": {**_OPTIONS, "result_directory": "out"}},
        "unknown-key": {"options": {**_OPTIONS, "peak_memroy": 3}},
        "wrong-type": {"options": {**_OPTIONS, "project": True}},
        "slack-clash": {"options": {**_OPTIONS, "slack_channel": "c"}},
        "tag-without-channel": {"options": {"project": "p", "slack_tag": "t"}},
    }


_DEFINITION_ERRORS = [
    "empty-name",
    "empty-env",
    "empty-produces",
    "produces-ends-in-a-slash",
    "produces-is-the-output-directory",
    "produces-is-absolute",
    "missing-spec",
    "spec-is-a-directory",
    "missing-branches",
    "branches-is-a-directory",
    "no-project",
    "step-owned-key",
    "unknown-key",
    "wrong-type",
    "slack-clash",
    "tag-without-channel",
]
"""The names of the cases :func:`_definition_errors` returns."""


def _value_error(model: _Model, **overrides: Any) -> str:
    """Define the step with ``overrides``; assert ValueError and return its message."""
    with pytest.raises(ValueError) as excinfo:
        _step(model, model.registry(), **overrides)
    return str(excinfo.value)


def _assert_allowed_keys_in_order(message: str) -> None:
    """Assert that ``message`` lists every allowed key, in sorted order, before any hint."""
    listing = message.partition("Did you mean")[0]
    position = 0
    for key in _ALLOWED:
        found = listing.find(key, position)
        assert found >= 0, (key, message)
        position = found + len(key)


def test_empty_name_or_env_is_rejected(model: _Model) -> None:
    """An empty name or env raises ValueError starting "Step '<name>':"."""
    assert _value_error(model, name="").startswith("Step '':")
    assert _value_error(model, name="toy", env="").startswith("Step 'toy':")


@pytest.mark.parametrize("produces", ["", "sims/toy/"])
def test_empty_produces_or_one_ending_in_a_slash_is_rejected(
    model: _Model, produces: str
) -> None:
    """produces='' or 'sims/toy/' raises ValueError starting "Step '<name>':"."""
    message = _value_error(model, produces=produces, name="toy")

    assert message.startswith("Step 'toy':"), message


@pytest.mark.parametrize("produces", [".", "..", "sims/../..", "/tmp/sims/toy"])
def test_produces_outside_the_output_directory_is_rejected(
    model: _Model, produces: str
) -> None:
    """produces '.', '..' or an absolute path outside the output directory raises ValueError starting "Step '<name>':", naming produces and the output directory and saying produces must name a directory under it."""
    registry = model.registry()
    output = model.base / "out" / "run1"

    with pytest.raises(ValueError) as excinfo:
        _step(model, registry, produces=produces, name="toy")

    message = str(excinfo.value)
    assert message.startswith("Step 'toy':"), message
    assert produces in message, message
    assert str(output) in message, message
    assert "under" in message, message
    with pytest.raises(WiringError):
        registry.resolve_input(produces)
    assert registry.key_for(registry.path_for(produces)) is None


@pytest.mark.parametrize("argument", ["spec", "branches"])
def test_a_missing_spec_or_branches_file_is_rejected(model: _Model, argument: str) -> None:
    """A spec or branches path that does not exist, or is a directory, raises ValueError starting "Step '<name>':" and naming the path."""
    for path in (model.base / "model" / "missing.yaml", model.base / "model"):
        message = _value_error(model, **{argument: path})

        assert message.startswith(f"Step '{_KEY}':"), message
        assert str(path) in message, message


def test_options_without_project_are_rejected(model: _Model) -> None:
    """options without 'project' raise ValueError starting "Step '<name>':" and naming 'project'."""
    message = _value_error(model, options={"no_slack": True, "queue": "all.q"})

    assert message.startswith(f"Step '{_KEY}':"), message
    assert "project" in message


@pytest.mark.parametrize(
    "key, instead",
    [
        ("model_specification", "spec="),
        ("branch_configuration", "branches="),
        ("artifact_path", "artifact="),
        ("result_directory", "itself"),
        ("run_config", "itself"),
    ],
)
def test_step_owned_option_keys_are_refused_naming_what_to_use_instead(
    model: _Model, key: str, instead: str
) -> None:
    """Each of model_specification, branch_configuration, artifact_path, result_directory and run_config raises ValueError naming the key and spec=, branches=, artifact=, or that the step sets it itself."""
    message = _value_error(model, options={**_OPTIONS, key: "some/path.yaml"})

    assert message.startswith(f"Step '{_KEY}':"), message
    assert key in message
    assert instead in message


def test_a_mistyped_option_key_gets_a_did_you_mean(model: _Model) -> None:
    """A key such as 'peak_memroy' raises ValueError naming it, listing the allowed keys in sorted order, and ending "Did you mean 'peak_memory'?"."""
    message = _value_error(model, options={**_OPTIONS, "peak_memroy": 3})

    assert message.startswith(f"Step '{_KEY}':"), message
    assert "peak_memroy" in message
    _assert_allowed_keys_in_order(message)
    assert message.endswith("Did you mean 'peak_memory'?"), message


@pytest.mark.parametrize("key", ["verbose", "pdb", "sim_verbosity"])
def test_psimulate_options_the_step_does_not_allow_are_refused_with_the_allowed_keys(
    model: _Model, key: str
) -> None:
    """The keys 'verbose', 'pdb' and 'sim_verbosity' each raise ValueError naming the key and listing the allowed keys in sorted order."""
    message = _value_error(model, options={**_OPTIONS, key: "1"})

    assert message.startswith(f"Step '{_KEY}':"), message
    assert key in message
    _assert_allowed_keys_in_order(message)


@pytest.mark.parametrize(
    "key, value, got, expects",
    [
        ("project", True, "bool", ("str", "int", "float")),
        ("project", None, "None", ("str", "int", "float")),
        ("queue", ["all.q"], "list", ("str", "int", "float")),
        ("project", Path("proj_simscience"), "Path", ("str", "int", "float")),
        ("no_slack", "yes", "str", ("bool",)),
    ],
    ids=["bool", "none", "list", "path", "no-slack-str"],
)
def test_option_values_of_the_wrong_type_are_rejected(
    model: _Model, key: str, value: object, got: str, expects: tuple[str, ...]
) -> None:
    """project=True, project=None, a list, a Path, and no_slack='yes' each raise ValueError naming the key, the type it got and the type it expects."""
    options: dict[str, Any] = {**_OPTIONS, key: value}

    message = _value_error(model, options=options)

    assert message.startswith(f"Step '{_KEY}':"), message
    assert key in message
    assert got in message
    for expected in expects:
        assert expected in message, (expected, message)


def test_no_slack_with_a_slack_channel_or_tag_is_rejected(model: _Model) -> None:
    """no_slack=True with slack_channel, or with slack_tag, raises ValueError naming the keys."""
    with_channel = _value_error(model, options={**_OPTIONS, "slack_channel": "c"})
    with_both = _value_error(
        model, options={**_OPTIONS, "slack_channel": "c", "slack_tag": "t"}
    )
    with_tag = _value_error(model, options={**_OPTIONS, "slack_tag": "t"})

    assert with_channel.startswith(f"Step '{_KEY}':"), with_channel
    assert "no_slack" in with_channel and "slack_channel" in with_channel
    assert with_both.startswith(f"Step '{_KEY}':"), with_both
    assert "no_slack" in with_both and "slack_tag" in with_both
    assert with_tag.startswith(f"Step '{_KEY}':"), with_tag
    assert "slack_tag" in with_tag
    allowed = {"project": "p", "no_slack": False, "slack_channel": "c", "slack_tag": "t"}
    assert _flags(_step(model, model.registry(), options=allowed))


def test_a_slack_tag_without_a_slack_channel_is_rejected(model: _Model) -> None:
    """slack_tag without slack_channel raises ValueError naming both keys."""
    message = _value_error(model, options={"project": "p", "slack_tag": "t"})

    assert message.startswith(f"Step '{_KEY}':"), message
    assert "slack_tag" in message
    assert "slack_channel" in message


@pytest.mark.parametrize("case", _DEFINITION_ERRORS)
def test_definition_errors_register_nothing(model: _Model, case: str) -> None:
    """After each ValueError above, resolve_input of produces still raises WiringError, so nothing was registered."""
    cases = _definition_errors(model)
    assert set(cases) == set(_DEFINITION_ERRORS)
    registry = model.registry()

    with pytest.raises(ValueError):
        _step(model, registry, **cases[case])

    with pytest.raises(WiringError):
        registry.resolve_input(_KEY)
    assert registry.key_for(registry.path_for(_KEY)) is None


def test_an_unknown_artifact_key_raises_a_wiring_error_naming_the_step(model: _Model) -> None:
    """An artifact key with no producer and no root input raises WiringError starting "Step '<name>':" and naming the key."""
    with pytest.raises(WiringError) as excinfo:
        _step(model, model.registry(), artifact="artifacts/missing.hdf")

    message = str(excinfo.value)
    assert message.startswith(f"Step '{_KEY}':"), message
    assert "artifacts/missing.hdf" in message


def test_produces_on_a_root_input_raises_a_wiring_error_naming_the_step(
    model: _Model,
) -> None:
    """produces equal to a declared root input's key raises WiringError starting "Step '<name>':"."""
    registry = model.registry({_KEY: model.spec})

    with pytest.raises(WiringError) as excinfo:
        _step(model, registry)

    assert str(excinfo.value).startswith(f"Step '{_KEY}':"), str(excinfo.value)


def test_produces_already_registered_as_a_file_output_raises_a_wiring_error_naming_the_step(
    model: _Model,
) -> None:
    """produces that another step registered as a file output raises WiringError starting "Step '<name>':" and naming the key."""
    registry = model.registry()
    registry.register_output(_KEY)

    with pytest.raises(WiringError) as excinfo:
        _step(model, registry, name="toy")

    message = str(excinfo.value)
    assert message.startswith("Step 'toy':"), message
    assert _KEY in message.removeprefix("Step 'toy':")


def test_a_simulation_step_sharing_a_name_with_another_step_is_reported_by_run(
    model: _Model,
) -> None:
    """run() of a pipeline with a simulation step and a python step of the same name raises PipelineError from the existing duplicate-name check, before anything runs."""
    registry = model.registry()
    tasks = [
        _step(model, registry),
        python_step(
            _constant, inputs={}, produces="other.parquet", registry=registry, name=_KEY
        ),
    ]

    with pytest.raises(PipelineError) as excinfo:
        run(Pipeline(tasks, model.state, registry))

    assert not isinstance(excinfo.value, StepFailed)
    assert f"Two steps are named {_KEY!r}" in str(excinfo.value)
    assert model.calls() == []
    assert not model.run_dir.exists()


# Runs through run() with the stub psimulate


def test_the_stub_on_the_env_path_runs_instead_of_an_installed_psimulate(
    model: _Model,
) -> None:
    """A run of a simulation step whose env is the stub env records one call in the stub's argv log, starting with 'run'."""
    assert run(_pipeline(model, consumer=False)) == {_KEY: TaskOutcome.SUCCESS}

    [argv] = model.calls()
    assert argv[0] == "run"
    assert argv[argv.index("-o") + 1] == str(model.scratch)


def test_a_run_moves_the_complete_run_to_the_run_directory_and_writes_the_sentinel(
    model: _Model,
) -> None:
    """run() gives the step SUCCESS; the run directory holds results/dead with 4 parquet files, metadata with 4 JSON files, and the sentinel, and the scratch directory is gone."""
    outcomes = run(_pipeline(model, consumer=False))

    assert outcomes == {_KEY: TaskOutcome.SUCCESS}
    assert len(list((model.run_dir / "results" / METRIC).glob("*.parquet"))) == 4
    assert len(list((model.run_dir / "metadata").glob("*.json"))) == 4
    assert (model.run_dir / _SENTINEL).is_file()
    assert not model.scratch.exists()


def test_a_run_with_an_artifact_root_input_passes_its_path_to_psimulate(
    model: _Model,
) -> None:
    """With artifact naming a root input, the stub's logged argv has '-i' followed by that file's path, and the run directory is named by nothing else."""
    artifact = model.base / "artifacts" / "model_artifact.hdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"an artifact")
    pipeline = _pipeline(
        model, consumer=False, root_inputs={"artifact": artifact}, artifact="artifact"
    )

    assert run(pipeline) == {_KEY: TaskOutcome.SUCCESS}

    [argv] = model.calls()
    assert argv[argv.index("-i") + 1] == str(artifact.resolve())
    recorded = json.loads(_sentinel(model))
    assert str(recorded["psimulate_run"]).startswith("model_artifact/"), recorded
    assert sorted(path.name for path in model.run_dir.parent.iterdir()) == ["toy"]


def test_a_downstream_python_step_receives_the_run_directory_and_reads_the_results(
    model: _Model,
) -> None:
    """A python_step that names produces runs after the simulation step, its target receives a Path equal to registry.path_for(produces), and its summary is computed from the stub's parquet files."""
    pipeline = _pipeline(model)
    simulation, consumer = pipeline.tasks

    assert run(pipeline) == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}

    assert consumer.inputs["simulation"] is simulation.outputs["__run__"]
    summary = _summary(model)
    assert set(summary["received"]) == {str(model.run_dir)}
    assert summary["is_path"].all()
    assert set(summary["files"]) == {4}
    assert summary_values(summary) == deaths_by_draw()


def test_a_notebook_step_that_names_the_key_gets_the_run_directory_as_a_str(
    model: _Model,
) -> None:
    """A notebook_step with inputs={'run_dir': produces} has the quoted run directory path as that parameter's value in its command."""
    registry = model.registry()
    simulation = _step(model, registry)
    source = write_source_notebook(model.base / "notebooks" / "report.ipynb", "run_dir = ''")

    report = notebook_step(
        source,
        executed="executed/report.ipynb",
        registry=registry,
        env=_NAMED_ENV,
        inputs={"run_dir": _KEY},
    )

    assert report.inputs["run_dir"] is simulation.outputs["__run__"]
    assert shlex.join(["-r", "run_dir", str(model.run_dir)]) in _command(report)


def test_paths_with_a_space_and_a_single_quote_run_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the registry root, the spec, the branches file and the env prefix under a directory whose name holds a space and a single quote, run() succeeds and the consumer reads the results."""
    model = _make_model(tmp_path.resolve() / "the team's runs", monkeypatch)

    outcomes = run(_pipeline(model))

    assert outcomes == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}
    assert "'" in str(model.run_dir) and " " in str(model.run_dir)
    assert (model.run_dir / _SENTINEL).is_file()
    summary = _summary(model)
    assert set(summary["received"]) == {str(model.run_dir)}
    assert summary_values(summary) == deaths_by_draw()


def test_a_rebuild_replaces_the_run_directory(model: _Model) -> None:
    """A second run() with always-stale code replaces the run directory: a file added to the old run directory is gone, the sentinel's content changed, and no '.previous' or scratch directory is left."""
    pipeline = _pipeline(model)
    _first_run(model, pipeline)
    added = model.run_dir / "only_in_the_old_run.txt"
    added.write_text("old")
    first = _sentinel(model)

    assert run(pipeline) == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}

    assert not added.exists()
    assert _sentinel(model) != first
    assert not model.run_dir.with_name("toy.previous").exists()
    assert not model.scratch.exists()
    assert len(model.calls()) == 2


# Failures through run()


def test_one_simulation_missing_fails_with_the_counts_and_keeps_the_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After one successful run, a run with one simulation unfinished raises StepFailed whose line gives '3 of 4', the partial run under the scratch directory and that the previous run was kept, without the command; the run directory and its sentinel are byte-identical, the partial run is still under scratch, and the consumer is SKIP_PREVIOUS_FAILED."""
    pipeline = _pipeline(model)
    before = _first_run(model, pipeline)
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")

    error = _step_failed(pipeline)

    [partial] = _runs_in(model.scratch)
    line = str(error)
    assert line.startswith(
        f"Step '{_KEY}' failed: psimulate finished 3 of 4 simulations; the partial run is "
        f"at {partial}."
    ), line
    assert line.endswith(model.kept()), line
    assert_no_command(line, pipeline, _KEY)
    assert snapshot(model.run_dir) == before
    assert len(list((partial / "results" / METRIC).glob("*.parquet"))) == 3
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_a_failing_simulation_step_shows_no_line_of_the_check(
    model: _Model, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When a run with one simulation unfinished fails, neither the StepFailed message, nor its cause's command, nor what pytask printed holds a line of the check script."""
    pipeline = _pipeline(model)
    _first_run(model, pipeline)
    capsys.readouterr()
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")

    error = _step_failed(pipeline)

    printed = capsys.readouterr()
    [cause] = [failure for failure in error.failures.values()]
    shown = "\n".join([str(error), repr(cause), printed.out, printed.err])
    assert "psimulate finished 3 of 4 simulations" in str(error)
    assert [line for line in _script_lines() if line in shown] == []


def test_a_psimulate_that_exits_non_zero_fails_with_its_exit_status_and_keeps_the_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After one successful run, a stub that exits with status 3 before writing anything raises StepFailed whose line says the step's command failed before writing a message, without blaming psimulate, gives 'status 3', names the scratch directory and that the previous run was kept, without the command; the run directory is byte-identical, scratch exists and holds no run, and the consumer is SKIP_PREVIOUS_FAILED."""
    pipeline = _pipeline(model)
    before = _first_run(model, pipeline)
    monkeypatch.setenv(EXIT_VARIABLE, "3")

    error = _step_failed(pipeline)

    line = str(error)
    assert line.startswith(
        f"Step '{_KEY}' failed: the simulation step's command failed (the command exited "
        "with status 3) before writing a message;"
    ), line
    assert "psimulate did not finish" not in line, line
    assert f"psimulate's logs are under {model.scratch}." in line
    assert line.endswith(model.kept()), line
    assert_no_command(line, pipeline, _KEY)
    assert snapshot(model.run_dir) == before
    _assert_no_run_in_scratch(model)
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_zero_run_directories_fail_naming_the_scratch_and_keep_the_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After one successful run, a stub that writes no run directory raises StepFailed whose line names the scratch directory and says the previous run was kept, without the command; the run directory is byte-identical, scratch exists and holds no run, and the consumer is SKIP_PREVIOUS_FAILED."""
    pipeline = _pipeline(model)
    before = _first_run(model, pipeline)
    monkeypatch.setenv(RUN_DIRECTORIES_VARIABLE, "0")

    error = _step_failed(pipeline)

    line = str(error)
    assert line.startswith(
        f"Step '{_KEY}' failed: psimulate wrote no run directory under {model.scratch}."
    ), line
    assert line.endswith(model.kept()), line
    assert_no_command(line, pipeline, _KEY)
    assert snapshot(model.run_dir) == before
    _assert_no_run_in_scratch(model)
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_two_run_directories_fail_naming_them_and_keep_the_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After one successful run, a stub that writes two run directories raises StepFailed whose line names both and says the previous run was kept, without the command; the run directory is byte-identical, both runs are still under scratch, and the consumer is SKIP_PREVIOUS_FAILED."""
    pipeline = _pipeline(model)
    before = _first_run(model, pipeline)
    monkeypatch.setenv(RUN_DIRECTORIES_VARIABLE, "2")

    error = _step_failed(pipeline)

    runs = _runs_in(model.scratch)
    line = str(error)
    assert len(runs) == 2
    assert line.startswith(
        f"Step '{_KEY}' failed: psimulate wrote 2 run directories under {model.scratch}"
    ), line
    for path in runs:
        assert str(path) in line
    assert line.endswith(model.kept()), line
    assert_no_command(line, pipeline, _KEY)
    assert snapshot(model.run_dir) == before
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_a_first_run_that_fails_says_there_was_no_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first run of a step, with one simulation unfinished, raises StepFailed whose line ends 'There was no previous run.', and the run directory does not exist."""
    pipeline = _pipeline(model)
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")

    error = _step_failed(pipeline)

    line = str(error)
    assert "psimulate finished 3 of 4 simulations" in line
    assert line.endswith(NO_PREVIOUS), line
    assert_no_command(line, pipeline, _KEY)
    assert not model.run_dir.exists()
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_a_run_directory_without_a_sentinel_does_not_count_as_a_previous_run(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a run directory that holds no sentinel before a failing run, the StepFailed line ends 'There was no previous run.'."""
    (model.run_dir / "results").mkdir(parents=True)
    (model.run_dir / "results" / "left_by_a_crash.parquet").write_bytes(b"partial")
    pipeline = _pipeline(model)
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")

    error = _step_failed(pipeline)

    line = str(error)
    assert "psimulate finished 3 of 4 simulations" in line
    assert line.endswith(NO_PREVIOUS), line
    assert "was kept" not in line


def _fail_with_one_simulation_unfinished(
    model: _Model, pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run ``pipeline`` with one simulation unfinished so it fails, then unset the knob."""
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")
    _step_failed(pipeline)
    assert (model.scratch / _MESSAGE_FILE).is_file()
    assert len(_runs_in(model.scratch)) == 1
    monkeypatch.delenv(UNFINISHED_VARIABLE)


def test_a_rerun_after_a_failed_run_clears_the_scratch_and_succeeds(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a run that fails with one simulation unfinished, a second run() with the knob unset gives both steps SUCCESS and writes the sentinel of a complete run, neither the scratch directory nor 'toy.previous' is left, and the stub's argv log has 2 entries."""
    pipeline = _pipeline(model)
    _fail_with_one_simulation_unfinished(model, pipeline, monkeypatch)

    outcomes = run(pipeline)

    assert outcomes == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}
    recorded = json.loads(_sentinel(model))
    assert recorded["simulations"] == 4, recorded
    assert len(list((model.run_dir / "results" / METRIC).glob("*.parquet"))) == 4
    assert not model.scratch.exists()
    assert not model.run_dir.with_name("toy.previous").exists()
    assert len(model.calls()) == 2
    assert summary_values(_summary(model)) == deaths_by_draw()


def test_a_rerun_whose_psimulate_exits_non_zero_does_not_report_the_earlier_message(
    model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a run that fails with one simulation unfinished and leaves the message file, a second run whose stub exits with status 3 raises StepFailed whose line says the step's command failed with 'status 3' and does not hold the earlier '3 of 4' message; the scratch directory holds neither the old message file nor a run."""
    pipeline = _pipeline(model)
    _fail_with_one_simulation_unfinished(model, pipeline, monkeypatch)
    monkeypatch.setenv(EXIT_VARIABLE, "3")

    error = _step_failed(pipeline)

    line = str(error)
    assert line.startswith(
        f"Step '{_KEY}' failed: the simulation step's command failed (the command exited "
        "with status 3) before writing a message;"
    ), line
    assert "3 of 4" not in line, line
    assert line.endswith(NO_PREVIOUS), line
    assert_no_command(line, pipeline, _KEY)
    _assert_no_run_in_scratch(model)
    assert not (model.scratch / _MESSAGE_FILE).exists()
    assert len(model.calls()) == 2
    assert error.outcomes[_CONSUMER] == TaskOutcome.SKIP_PREVIOUS_FAILED


# describe_simulation_failure on its own


def _write_message(model: _Model, text: str) -> None:
    """Leave ``text`` in the scratch directory's message file, as a failed check does."""
    model.scratch.mkdir(parents=True, exist_ok=True)
    (model.scratch / _MESSAGE_FILE).write_text(text)


_COUNTS_MESSAGE = (
    "psimulate finished 1 of 4 simulations; the partial run is at /elsewhere. There was no "
    "previous run."
)
"""A check's message for a message file that a test writes itself."""


def test_describe_simulation_failure_is_none_for_a_task_that_is_not_a_simulation_step(
    model: _Model,
) -> None:
    """describe_simulation_failure returns None for a python step and for a plain command task, whatever the error."""
    registry = model.registry()
    _write_message(model, _COUNTS_MESSAGE)
    python = python_step(_constant, inputs={}, produces="table.parquet", registry=registry)
    plain = Task(
        name="plain",
        run="exit 1",
        inputs={},
        outputs={"out": file_node(model.base / "plain.txt")},
        resources={},
        env=None,
    )
    errors: list[BaseException] = [
        subprocess.CalledProcessError(1, "exit 1"),
        FileNotFoundError("gone"),
        _RemoteFailure("subprocess.CalledProcessError"),
    ]

    for task in (python, plain):
        for error in errors:
            assert describe_simulation_failure(task, error) is None, (task.name, error)


def test_describe_simulation_failure_ignores_the_message_file_for_an_error_that_is_not_a_command_failure(
    model: _Model,
) -> None:
    """With a message file in the scratch directory, describe_simulation_failure returns None for a FileNotFoundError and for a remote failure whose exc_type is not 'subprocess.CalledProcessError'."""
    task = _step(model, model.registry())
    _write_message(model, _COUNTS_MESSAGE)

    assert describe_simulation_failure(task, FileNotFoundError("the env is gone")) is None
    assert describe_simulation_failure(task, _RemoteFailure("FileNotFoundError")) is None


def test_describe_simulation_failure_reads_the_message_file_for_a_remote_command_failure(
    model: _Model,
) -> None:
    """For an error object whose exc_type is 'subprocess.CalledProcessError', describe_simulation_failure returns the message file's text."""
    task = _step(model, model.registry())
    _write_message(model, _COUNTS_MESSAGE)

    described = describe_simulation_failure(
        task, _RemoteFailure("subprocess.CalledProcessError")
    )

    assert described == _COUNTS_MESSAGE


def test_describe_simulation_failure_gives_the_bridge_line_without_a_message_file(
    model: _Model,
) -> None:
    """For an error object whose exc_type is 'subprocess.CalledProcessError' and no message file, the description says the step's command failed before writing a message, without blaming psimulate, names the scratch directory and the previous-run state, and gives no exit status."""
    task = _step(model, model.registry())
    error = _RemoteFailure("subprocess.CalledProcessError")

    first = describe_simulation_failure(task, error)
    model.run_dir.mkdir(parents=True)
    (model.run_dir / _SENTINEL).write_text('{"run_id": "earlier"}')
    later = describe_simulation_failure(task, error)

    for described in (first, later):
        assert described is not None
        assert described.startswith(
            "the simulation step's command failed before writing a message;"
        ), described
        assert "psimulate did not finish" not in described
        assert f"psimulate's logs are under {model.scratch}." in described
        assert "status" not in described
        assert "psimulate run" not in described
    assert first is not None and first.endswith(NO_PREVIOUS), first
    assert later is not None and later.endswith(model.kept()), later


# Staleness, with a stable code identity on every step


def _reasons(plan: list[StepPreview]) -> dict[str, list[Reason]]:
    """Map each planned step's name to its reasons."""
    return {step.name: step.reasons for step in plan}


def _would_run(plan: list[StepPreview]) -> set[str]:
    """Return the names of the planned steps that would run."""
    return {step.name for step in plan if step.would_run}


def test_an_unchanged_pipeline_skips_both_steps_and_does_not_call_psimulate_again(
    model: _Model,
) -> None:
    """A second run() gives both steps SKIP_UNCHANGED, collects without error although the run directory exists, and the stub's argv log still has one entry."""
    pipeline = _pipeline(model, stable=True)
    _first_run(model, pipeline)

    outcomes = run(pipeline)

    assert outcomes == {
        _KEY: TaskOutcome.SKIP_UNCHANGED,
        _CONSUMER: TaskOutcome.SKIP_UNCHANGED,
    }
    assert len(model.calls()) == 1


def test_removing_the_sentinel_runs_the_simulation_and_its_consumer_again(
    model: _Model,
) -> None:
    """After the sentinel is removed, run() runs both the simulation step and its consumer."""
    pipeline = _pipeline(model, stable=True)
    _first_run(model, pipeline)
    (model.run_dir / _SENTINEL).unlink()

    outcomes = run(pipeline)

    assert outcomes == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}
    assert len(model.calls()) == 2
    assert (model.run_dir / _SENTINEL).is_file()


def test_a_changed_sentinel_makes_preview_report_the_artifact_changed_and_a_cascade(
    model: _Model,
) -> None:
    """After other content is written to the sentinel, preview reports both steps would run, the simulation step's reasons include Reason("artifact 'sims/toy'", "changed"), the consumer's are [Reason("step 'sims/toy'", "cascade")], and no reason holds the run directory's path."""
    pipeline = _pipeline(model, stable=True)
    _first_run(model, pipeline)
    (model.run_dir / _SENTINEL).write_text('{"run_id": "written by hand"}')

    plan = preview(pipeline)

    reasons = _reasons(plan)
    assert _would_run(plan) == {_KEY, _CONSUMER}
    assert Reason(f"artifact {_KEY!r}", "changed") in reasons[_KEY]
    assert reasons[_CONSUMER] == [Reason(f"step {_KEY!r}", "cascade")]
    for step in plan:
        for reason in step.reasons:
            assert str(model.run_dir) not in reason.node, reason
            assert model.run_dir.as_posix() not in reason.node, reason


def test_a_changed_option_makes_preview_report_the_command_changed(model: _Model) -> None:
    """After an option changes, preview reports the simulation step would run with "command of 'sims/toy'" among its reasons."""
    _first_run(model, _pipeline(model, stable=True))
    changed = _pipeline(model, stable=True, options={**_OPTIONS, "queue": "all.q"})

    plan = preview(changed)

    assert _KEY in _would_run(plan)
    assert Reason(f"command of {_KEY!r}", "changed") in _reasons(plan)[_KEY]


def test_an_edited_spec_makes_preview_report_the_spec_changed(model: _Model) -> None:
    """After the spec file is edited, preview reports the simulation step would run with the spec's path among its changed reasons."""
    pipeline = _pipeline(model, stable=True)
    _first_run(model, pipeline)

    with Perturbed(model.spec, b"\n# an edit to the spec\n"):
        plan = preview(pipeline)

    assert _KEY in _would_run(plan)
    assert Reason(model.spec.resolve().as_posix(), "changed") in _reasons(plan)[_KEY]


def test_forcing_the_simulation_rewrites_the_sentinel_and_reruns_the_consumer(
    model: _Model,
) -> None:
    """run(force='sims/toy') runs the simulation again, the sentinel's content changes, and the consumer runs again instead of SKIP_UNCHANGED."""
    pipeline = _pipeline(model, stable=True)
    _first_run(model, pipeline)
    first = _sentinel(model)

    outcomes = run(pipeline, force=_KEY)

    assert outcomes == {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}
    assert _sentinel(model) != first
    assert len(model.calls()) == 2


# The dagger command


@dataclass(frozen=True)
class _Example:
    """A copy of the example definition whose env is a stub env."""

    definition: Path
    log: Path

    @property
    def output(self) -> Path:
        """Return the directory the copy's registry writes under."""
        return self.definition.parent / "output" / "example"

    @property
    def run_dir(self) -> Path:
        """Return the copy's run directory."""
        return self.output / _KEY

    @property
    def scratch(self) -> Path:
        """Return the copy's scratch directory."""
        return scratch_for(self.run_dir)

    def kept(self) -> str:
        """Return the sentence that says the previous complete run was kept."""
        return f"The previous complete run at {self.run_dir} was kept."


@pytest.fixture
def example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Example]:
    """Copy the example with a stub env, and drop the definition modules the test loads."""
    base = tmp_path.resolve()
    log = base / "psimulate_calls.jsonl"
    monkeypatch.setenv(LOG_VARIABLE, str(log))
    for variable in KNOB_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    # Loading a definition file imports it as a module named by its stem, 'pipeline'.
    monkeypatch.delitem(sys.modules, "pipeline", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    modules = set(sys.modules)
    definition = copy_simulation_example(base / "example", make_stub_env(base / "env"))
    yield _Example(definition=definition, log=log)
    for name in set(sys.modules) - modules:
        file = getattr(sys.modules.get(name), "__file__", None)
        if isinstance(file, str) and Path(file).resolve().is_relative_to(base):
            del sys.modules[name]


def _dagger(*args: str) -> Result:
    """Run ``dagger`` with ``args`` in this process."""
    return CliRunner().invoke(dagger, list(args))


def _shown(result: Result) -> str:
    """Return ``result``'s output and any exception it caught, for an assert message."""
    text = result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += "".join(traceback.format_exception(result.exception))
    return text


def _dagger_run(example: _Example) -> Result:
    """Run 'dagger run -c <definition> --local' on the example's copy."""
    return _dagger("run", "-c", str(example.definition), "--local")


def _failing_rerun(
    example: _Example, monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> tuple[Result, dict[str, bytes]]:
    """Run the copy once, then again with the stub knob ``variable`` set to ``value``.

    Returns the second run's result and the run directory's bytes after the first run.
    """
    first = _dagger_run(example)
    assert first.exit_code == 0, _shown(first)
    before = snapshot(example.run_dir)
    monkeypatch.setenv(variable, value)
    return _dagger_run(example), before


def _assert_reported_failure(
    result: Result, example: _Example, before: dict[str, bytes]
) -> str:
    """Assert dagger's common report of a failed simulation step; return its stderr."""
    errors = result.stderr
    assert result.exit_code == 1, _shown(result)
    assert f"Step '{_KEY}' failed: " in errors, errors
    assert example.kept() in errors, errors
    assert f"Not run because an upstream step failed: {_CONSUMER!r}." in errors, errors
    for fragment in COMMAND_FRAGMENTS:
        assert fragment not in errors, (fragment, errors)
    assert snapshot(example.run_dir) == before
    return errors


def test_dagger_run_of_a_copy_of_the_example_succeeds_with_the_stub_env(
    example: _Example,
) -> None:
    """'dagger run -c <copy>/pipeline.py --local' on a copy of the example with its env replaced by the stub env exits 0, and the run directory, the sentinel and summary.csv exist."""
    result = _dagger_run(example)

    assert result.exit_code == 0, _shown(result)
    assert len(read_argv_log(example.log)) == 1
    assert (example.run_dir / _SENTINEL).is_file()
    assert len(list((example.run_dir / "results" / METRIC).glob("*.parquet"))) == 4
    summary = pd.read_csv(example.output / "summary.csv")
    assert summary_values(summary) == deaths_by_draw()


def test_dagger_preview_of_a_copy_of_the_example_lists_both_steps(example: _Example) -> None:
    """'dagger preview -c <copy>/pipeline.py' exits 0 and lists 'sims/toy' and 'summary'."""
    result = _dagger("preview", "-c", str(example.definition))

    assert result.exit_code == 0, _shown(result)
    listed = {
        line.split()[1].rstrip(":")
        for line in result.stdout.splitlines()
        if line.startswith(("run ", "skip "))
    }
    assert listed == {_KEY, _CONSUMER}
    assert read_argv_log(example.log) == []


def test_dagger_run_with_one_simulation_missing_exits_one_with_the_counts(
    example: _Example, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful 'dagger run', a run with one simulation unfinished exits 1, its output gives the step's line with '3 of 4' and the paths but not the command, and the summary step did not run."""
    result, before = _failing_rerun(example, monkeypatch, UNFINISHED_VARIABLE, "1")

    errors = _assert_reported_failure(result, example, before)
    [partial] = _runs_in(example.scratch)
    assert (
        f"psimulate finished 3 of 4 simulations; the partial run is at {partial}." in errors
    ), errors


def test_dagger_run_with_a_psimulate_that_exits_non_zero_exits_one_with_its_status(
    example: _Example, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful 'dagger run', a run whose stub exits with status 3 before writing exits 1, and its output gives the step's line with 'status 3' and the scratch directory but not the command."""
    result, before = _failing_rerun(example, monkeypatch, EXIT_VARIABLE, "3")

    errors = _assert_reported_failure(result, example, before)
    assert (
        "the simulation step's command failed (the command exited with status 3) before "
        "writing a message;" in errors
    ), errors
    assert f"psimulate's logs are under {example.scratch}." in errors, errors


def test_dagger_run_with_zero_run_directories_exits_one_naming_the_scratch(
    example: _Example, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful 'dagger run', a run whose stub writes no run directory exits 1, and its output gives the step's line naming the scratch directory but not the command."""
    result, before = _failing_rerun(example, monkeypatch, RUN_DIRECTORIES_VARIABLE, "0")

    errors = _assert_reported_failure(result, example, before)
    assert f"psimulate wrote no run directory under {example.scratch}." in errors, errors


def test_dagger_run_with_two_run_directories_exits_one_naming_them(
    example: _Example, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful 'dagger run', a run whose stub writes two run directories exits 1, and its output gives the step's line naming both but not the command."""
    result, before = _failing_rerun(example, monkeypatch, RUN_DIRECTORIES_VARIABLE, "2")

    errors = _assert_reported_failure(result, example, before)
    runs = _runs_in(example.scratch)
    assert len(runs) == 2
    assert f"psimulate wrote 2 run directories under {example.scratch}" in errors, errors
    for path in runs:
        assert str(path) in errors, errors
