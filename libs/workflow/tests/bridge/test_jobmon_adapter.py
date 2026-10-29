"""Tests for the bridge's Jobmon adapter (``bridge._jobmon_adapter``).

The tests marked ``requires_jobmon`` replace ``jobmon.client.api.Tool`` with a recording
fake, so they contact no Jobmon server; they run only where jobmon is installed.
"""

import ast
import importlib.metadata
import importlib.util
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, NoReturn

import pytest

from vivarium.workflow import harness
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import (
    MAX_NAME_LENGTH,
    TOOL_NAME,
    JobmonBindError,
    JobmonEnvironmentError,
    JobmonRun,
    PlanEntry,
    bind,
    check_environment,
    family,
    jobmon_name,
    run,
)

requires_jobmon = pytest.mark.skipif(
    importlib.util.find_spec("jobmon") is None, reason="jobmon is not installed"
)
"""Skip a test where jobmon cannot be imported, as in GitHub CI."""

_SUPPORTED_VERSIONS = {
    "jobmon_client": "3.7.9",
    "jobmon_core": "3.7.19",
    "jobmon_slurm": "1.15.0",
}
"""Versions in the supported series, as jobmon_installer_ihme 10.12.2 installs them."""

_CLUSTER_EXTRA = "vivarium-workflow[cluster]"
"""The extra that installs the supported Jobmon set, which a failed check names."""

_IMPORT_CALLS = {"importlib.import_module", "import_module", "__import__"}
"""Calls that import the module named by their first argument."""

_WORKFLOW_ID = 4242
"""The id that a recording fake workflow gets when it is bound."""

_BIND_OPTIONS: dict[str, Any] = {
    "cluster": "multiprocess",
    "default_resources": {"queue": "null.q"},
    "max_attempts": 3,
    "resource_scales": None,
    "workflow_name": "bridge_test",
}
"""Options for a bind, which a test overrides where it checks one."""


def _entry(
    name: str, *upstreams: str, resources: Mapping[str, Any] | None = None
) -> PlanEntry:
    """Return a plan entry for step ``name`` that waits for ``upstreams``."""
    return PlanEntry(
        signature=f"sig-{name}",
        name=name,
        command=f"echo {name}",
        upstreams=tuple(f"sig-{upstream}" for upstream in upstreams),
        resources={} if resources is None else resources,
    )


# Stand-ins for the environment check


def _distribution_key(name: str) -> str:
    """Return ``name`` normalized, so that ``jobmon-client`` matches ``jobmon_client``."""
    return re.sub(r"[-_.]+", "_", name).lower()


class _Distribution:
    """Stand in for the metadata of an installed distribution."""

    def __init__(self, name: str, version: str) -> None:
        self.version = version
        self.metadata = {"Name": name, "Version": version}


def _report_versions(monkeypatch: pytest.MonkeyPatch, versions: Mapping[str, str]) -> None:
    """Make importlib.metadata report ``versions`` as the installed Jobmon set."""
    real_version = importlib.metadata.version
    real_distribution = importlib.metadata.distribution
    real_metadata = importlib.metadata.metadata

    def is_jobmon(name: str) -> bool:
        """Check whether ``name`` is a Jobmon distribution."""
        return _distribution_key(name).startswith("jobmon")

    def jobmon_version(name: str) -> str:
        """Return a Jobmon distribution's reported version, or raise as for a missing one."""
        key = _distribution_key(name)
        if key not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return versions[key]

    def version(name: str) -> str:
        """Stand in for importlib.metadata.version."""
        return jobmon_version(name) if is_jobmon(name) else real_version(name)

    def distribution(name: str) -> Any:
        """Stand in for importlib.metadata.distribution."""
        if is_jobmon(name):
            return _Distribution(name, jobmon_version(name))
        return real_distribution(name)

    def metadata(name: str) -> Any:
        """Stand in for importlib.metadata.metadata."""
        if is_jobmon(name):
            return _Distribution(name, jobmon_version(name)).metadata
        return real_metadata(name)

    for attribute, fake, original in (
        ("version", version, real_version),
        ("distribution", distribution, real_distribution),
        ("metadata", metadata, real_metadata),
    ):
        monkeypatch.setattr(importlib.metadata, attribute, fake)
        # The adapter may hold its own reference, from "from importlib.metadata import ...".
        if getattr(_jobmon_adapter, attribute, None) is original:
            monkeypatch.setattr(_jobmon_adapter, attribute, fake)


def _scontrol_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a stand-in scontrol first on PATH."""
    bin_dir = tmp_path / "slurm_bin"
    bin_dir.mkdir()
    scontrol = bin_dir / "scontrol"
    scontrol.write_text("#!/bin/sh\nexit 0\n")
    scontrol.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _nothing_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave only an empty directory on PATH, so that no scontrol is found."""
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


# Reading the modules' imports


def _imported_modules(node: ast.AST) -> list[str]:
    """Return the modules that ``node`` imports, if it is an import or an import call."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module] if node.module is not None and node.level == 0 else []
    if isinstance(node, ast.Call) and node.args and ast.unparse(node.func) in _IMPORT_CALLS:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return [first.value]
    return []


def _jobmon_imports(path: Path) -> list[tuple[str, int, bool]]:
    """Return each jobmon import in a module as (file name, line, inside a function)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    in_functions = {
        id(inner)
        for outer in ast.walk(tree)
        if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef))
        for inner in ast.walk(outer)
        if inner is not outer
    }
    return [
        (path.name, getattr(node, "lineno", 0), id(node) in in_functions)
        for node in ast.walk(tree)
        if any(module.partition(".")[0] == "jobmon" for module in _imported_modules(node))
    ]


# Stand-ins for a bound workflow


class _StandInTask:
    """Stand in for a Jobmon task, whose final status is set when its workflow runs."""

    final_status: str


class _StandInWorkflow:
    """Stand in for a bound Jobmon workflow whose run ends with given statuses."""

    def __init__(self, status: str, final_statuses: Mapping[_StandInTask, str]) -> None:
        self.status = status
        self.final_statuses = final_statuses
        self.fail_fast: list[bool] = []

    def run(self, fail_fast: bool = False, *args: Any, **kwargs: Any) -> str:
        """Record ``fail_fast``, set each task's final status, and return a status."""
        self.fail_fast.append(fail_fast)
        for task, status in self.final_statuses.items():
            task.final_status = status
        return self.status


# A recording fake of jobmon's Tool


class _FakeTask:
    """Stand in for a Jobmon task, recording how its template created it."""

    def __init__(
        self,
        template: "_FakeTemplate",
        name: str | None,
        upstream_tasks: Iterable[Any] | None,
        kwargs: dict[str, Any],
    ) -> None:
        self.template = template
        self.name = name
        self.upstream_tasks = list(upstream_tasks or [])
        self.kwargs = kwargs
        args = {key: value for key, value in kwargs.items() if key in template.arg_names}
        self.command = template.command_template.format(**args)

    def add_upstream(self, task: Any) -> None:
        """Make this task wait for ``task``."""
        self.upstream_tasks.append(task)


class _FakeTemplate:
    """Stand in for a Jobmon task template, which renders each task's command."""

    def __init__(self, name: str, command_template: str, arg_names: set[str]) -> None:
        self.name = name
        self.command_template = command_template
        self.arg_names = arg_names

    def create_task(
        self,
        name: str | None = None,
        upstream_tasks: Iterable[Any] | None = None,
        **kwargs: Any,
    ) -> _FakeTask:
        """Return a task that records how it was created."""
        return _FakeTask(self, name, upstream_tasks, kwargs)


class _FakeWorkflow:
    """Stand in for a Jobmon workflow, recording how it was created, bound and run."""

    def __init__(self, workflow_args: str, name: str, kwargs: dict[str, Any]) -> None:
        self.workflow_args = workflow_args
        self.name = name
        self.kwargs = kwargs
        self.added: list[Any] = []
        self.bound = False
        self.runs = 0

    @property
    def workflow_id(self) -> int:
        """Return the workflow's id, which it has only once it is bound, as in Jobmon."""
        if not self.bound:
            raise RuntimeError("The workflow has no id until it is bound.")
        return _WORKFLOW_ID

    def add_task(self, task: Any) -> Any:
        """Add one task."""
        self.added.append(task)
        return task

    def add_tasks(self, tasks: Iterable[Any]) -> None:
        """Add several tasks."""
        self.added.extend(tasks)

    def bind(self) -> None:
        """Bind the workflow, which gives it its id."""
        self.bound = True

    def run(self, *args: Any, **kwargs: Any) -> str:
        """Count the run, which bind must not start."""
        self.runs += 1
        return "D"


class _FakeTool:
    """Stand in for ``jobmon.client.api.Tool``, recording what the adapter asks of it."""

    instances: ClassVar[list["_FakeTool"]] = []
    """Every tool made, in a list that each test replaces."""

    def __init__(self, name: str = "unknown", *args: Any, **kwargs: Any) -> None:
        self.name = name
        self.template_requests: list[str] = []
        self.templates: dict[str, _FakeTemplate] = {}
        self.workflows: list[_FakeWorkflow] = []
        _FakeTool.instances.append(self)

    def get_task_template(
        self,
        template_name: str,
        command_template: str = "",
        node_args: Iterable[str] | None = None,
        task_args: Iterable[str] | None = None,
        op_args: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> _FakeTemplate:
        """Return the template of this name, made on its first request, as Jobmon does."""
        self.template_requests.append(template_name)
        if template_name not in self.templates:
            names = {*(node_args or ()), *(task_args or ()), *(op_args or ())}
            self.templates[template_name] = _FakeTemplate(
                template_name, command_template, names
            )
        return self.templates[template_name]

    def create_workflow(
        self, workflow_args: str = "", name: str = "", **kwargs: Any
    ) -> _FakeWorkflow:
        """Return a new workflow that records how it was created."""
        workflow = _FakeWorkflow(workflow_args, name, kwargs)
        self.workflows.append(workflow)
        return workflow


def _record_tools(monkeypatch: pytest.MonkeyPatch) -> list[_FakeTool]:
    """Replace jobmon's Tool with the recording fake, and return the tools that get made."""
    made: list[_FakeTool] = []
    monkeypatch.setattr(_FakeTool, "instances", made)
    for module_name in ("jobmon.client.api", "jobmon.client.tool"):
        monkeypatch.setattr(importlib.import_module(module_name), "Tool", _FakeTool)
    return made


def _bind(plan: Sequence[PlanEntry], **options: Any) -> JobmonRun:
    """Bind ``plan`` with the test's options, changed by ``options``."""
    return bind(plan, **{**_BIND_OPTIONS, **options})


def _requested_templates(tools: Iterable[_FakeTool]) -> set[str]:
    """Return the names of the task templates that the adapter asked ``tools`` for."""
    return {name for tool in tools for name in tool.template_requests}


class _InvalidRequest(Exception):
    """Stand in for ``jobmon.core.exceptions.InvalidRequest``."""


def _raising(error: BaseException) -> Callable[..., NoReturn]:
    """Return a stand-in method that raises ``error``, as a refused Jobmon call does."""

    def raising(*args: Any, **kwargs: Any) -> NoReturn:
        """Raise the error."""
        raise error

    return raising


# Names


@pytest.mark.parametrize(
    "name, expected",
    [
        ("pafs[ETH]", "pafs"),
        ("mid.parquet", "mid.parquet"),
        ("grid[1][2]", "grid[1]"),
        ("pafs[ETH]_v2", "pafs[ETH]_v2"),
    ],
)
def test_family_drops_one_trailing_bracketed_part(name: str, expected: str) -> None:
    """family removes one trailing [...] part and leaves a name without one unchanged."""
    assert family(name) == expected


@pytest.mark.parametrize("character", ["/", "\\", "'", '"', " "])
def test_jobmon_name_replaces_each_character_jobmon_rejects(character: str) -> None:
    """jobmon_name turns each character Jobmon rejects into '_' and keeps the rest of the name."""
    assert jobmon_name(f"pafs{character}ETH{character}v2") == "pafs_ETH_v2"


def test_jobmon_name_guards_a_leading_digit() -> None:
    """jobmon_name gives a name that starts with a digit a leading '_'."""
    assert jobmon_name("2024_pafs") == "_2024_pafs"
    assert jobmon_name("pafs_2024") == "pafs_2024"


def test_jobmon_name_cuts_a_long_name_to_the_maximum_length() -> None:
    """jobmon_name returns at most MAX_NAME_LENGTH characters for a longer name and leaves a shorter one's length alone."""
    long_name = "".join(chr(ord("a") + index % 26) for index in range(MAX_NAME_LENGTH + 50))

    assert jobmon_name(long_name) == long_name[:MAX_NAME_LENGTH]
    assert jobmon_name(long_name[:MAX_NAME_LENGTH]) == long_name[:MAX_NAME_LENGTH]
    assert jobmon_name("short") == "short"


@requires_jobmon
def test_every_jobmon_name_passes_jobmons_own_check() -> None:
    """For names with '/', a leading digit, brackets, spaces, quotes and more than 250 characters, jobmon_name's result passes jobmon's own Task.is_valid_job_name."""
    jobmon_task = importlib.import_module("jobmon.client.task").Task
    names = [
        "pafs/ETH",
        "2024_pafs",
        "pafs[ETH]",
        "pafs ETH",
        "it's",
        'say "pafs"',
        "back\\slash",
        "x" * (MAX_NAME_LENGTH + 50),
        "1 a/b[c]" + "y" * MAX_NAME_LENGTH,
    ]

    for name in names:
        assert jobmon_task.is_valid_job_name(jobmon_name(name)), name


# The environment check


def test_check_environment_accepts_the_supported_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With importlib.metadata reporting jobmon_client 3.7.9, jobmon_core 3.7.19 and jobmon_slurm 1.15.0 and scontrol on PATH, check_environment('slurm') returns None."""
    _report_versions(monkeypatch, _SUPPORTED_VERSIONS)
    _scontrol_on_path(tmp_path, monkeypatch)

    check_environment("slurm")


def test_check_environment_rejects_a_core_outside_its_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With jobmon_core 3.18.0, check_environment raises JobmonEnvironmentError whose message names 3.18.0, the other installed versions, and 'vivarium-workflow[cluster]'."""
    _report_versions(monkeypatch, {**_SUPPORTED_VERSIONS, "jobmon_core": "3.18.0"})
    _scontrol_on_path(tmp_path, monkeypatch)

    with pytest.raises(JobmonEnvironmentError) as info:
        check_environment("slurm")

    message = str(info.value)
    for named in ("3.18.0", "3.7.9", "1.15.0", _CLUSTER_EXTRA):
        assert named in message, message


def test_check_environment_reports_jobmon_missing_naming_the_cluster_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When importlib.metadata finds no jobmon_client, check_environment raises JobmonEnvironmentError naming 'vivarium-workflow[cluster]'."""
    without_client = {k: v for k, v in _SUPPORTED_VERSIONS.items() if k != "jobmon_client"}
    _report_versions(monkeypatch, without_client)
    _scontrol_on_path(tmp_path, monkeypatch)

    with pytest.raises(JobmonEnvironmentError, match=re.escape(_CLUSTER_EXTRA)):
        check_environment("slurm")


def test_check_environment_on_slurm_without_scontrol_names_opt_slurm_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With supported versions and shutil.which('scontrol') returning None, check_environment('slurm') raises JobmonEnvironmentError naming /opt/slurm/bin."""
    _report_versions(monkeypatch, _SUPPORTED_VERSIONS)
    _nothing_on_path(tmp_path, monkeypatch)

    with pytest.raises(JobmonEnvironmentError, match=re.escape("/opt/slurm/bin")):
        check_environment("slurm")


def test_check_environment_off_slurm_needs_neither_scontrol_nor_jobmon_slurm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With supported client and core, no jobmon_slurm and no scontrol, check_environment('multiprocess') returns None."""
    _report_versions(monkeypatch, {"jobmon_client": "3.7.9", "jobmon_core": "3.7.19"})
    _nothing_on_path(tmp_path, monkeypatch)

    check_environment("multiprocess")


@pytest.mark.parametrize("slurm_version", ["2.0.0", None])
def test_check_environment_on_slurm_rejects_a_missing_or_unsupported_jobmon_slurm(
    slurm_version: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With jobmon_client 3.7.9, jobmon_core 3.7.19, scontrol on PATH, and jobmon_slurm 2.0.0 or not installed, check_environment('slurm') raises JobmonEnvironmentError whose message names jobmon_slurm, its version when installed, and 'vivarium-workflow[cluster]'."""
    versions = {"jobmon_client": "3.7.9", "jobmon_core": "3.7.19"}
    if slurm_version is not None:
        versions["jobmon_slurm"] = slurm_version
    _report_versions(monkeypatch, versions)
    _scontrol_on_path(tmp_path, monkeypatch)

    with pytest.raises(JobmonEnvironmentError) as info:
        check_environment("slurm")

    message = str(info.value)
    assert re.search(r"jobmon[-_]slurm", message), message
    assert _CLUSTER_EXTRA in message, message
    if slurm_version is not None:
        assert slurm_version in message, message


def test_check_environment_off_slurm_ignores_an_unsupported_jobmon_slurm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With jobmon_client 3.7.9, jobmon_core 3.7.19, jobmon_slurm 2.0.0 and scontrol on PATH, check_environment('multiprocess') returns None."""
    _report_versions(monkeypatch, {**_SUPPORTED_VERSIONS, "jobmon_slurm": "2.0.0"})
    _scontrol_on_path(tmp_path, monkeypatch)

    check_environment("multiprocess")


def test_jobmon_is_imported_only_inside_functions_of_the_adapter() -> None:
    """An ast walk over every module under vivarium/workflow/bridge and over harness.py finds jobmon imported only in _jobmon_adapter.py, and there only inside function bodies."""
    bridge_modules = sorted(Path(_jobmon_adapter.__file__).parent.rglob("*.py"))
    modules = [*bridge_modules, Path(harness.__file__)]
    names = {path.name for path in modules}
    imports = [found for path in modules for found in _jobmon_imports(path)]

    assert {"_jobmon_adapter.py", "execute.py", "plugin.py", "harness.py"} <= names
    assert any(name == "_jobmon_adapter.py" for name, _, _ in imports), imports
    misplaced = [
        (name, line)
        for name, line, in_function in imports
        if name != "_jobmon_adapter.py" or not in_function
    ]
    assert misplaced == []


# Running a bound workflow


def test_run_sets_the_workflow_status_and_each_task_status() -> None:
    """run, given a JobmonRun with a stand-in workflow whose run returns 'D' and stand-in tasks with final statuses, sets status 'D' and task_statuses to each task's final status by signature."""
    plan = (_entry("a"), _entry("b", "a"))
    tasks = {entry.signature: _StandInTask() for entry in plan}
    workflow = _StandInWorkflow("D", {task: "D" for task in tasks.values()})
    jobmon_run = JobmonRun(plan=plan, workflow_id=11, workflow=workflow, tasks=tasks)

    run(jobmon_run)

    assert jobmon_run.status == "D"
    assert jobmon_run.task_statuses == {"sig-a": "D", "sig-b": "D"}


def test_run_keeps_running_tasks_that_do_not_wait_on_a_failure() -> None:
    """run calls the workflow's run with fail_fast off, so Jobmon finishes independent tasks after one fails."""
    plan = (_entry("fails"), _entry("after", "fails"), _entry("independent"))
    tasks = {entry.signature: _StandInTask() for entry in plan}
    statuses = {"sig-fails": "F", "sig-after": "G", "sig-independent": "D"}
    final = {tasks[signature]: status for signature, status in statuses.items()}
    workflow = _StandInWorkflow("E", final)
    jobmon_run = JobmonRun(plan=plan, workflow_id=12, workflow=workflow, tasks=tasks)

    run(jobmon_run)

    assert workflow.fail_fast == [False]
    assert jobmon_run.status == "E"
    assert jobmon_run.task_statuses == statuses


# Binding, with a recording fake jobmon Tool


@requires_jobmon
def test_bind_makes_one_task_template_per_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """Binding entries named pafs[ETH], pafs[NGA] and mid.parquet asks the tool for two task templates, one per family."""
    tools = _record_tools(monkeypatch)

    tasks = _bind([_entry("pafs[ETH]"), _entry("pafs[NGA]"), _entry("mid.parquet")]).tasks

    assert len(_requested_templates(tools)) == 2, _requested_templates(tools)
    assert tasks["sig-pafs[ETH]"].template is tasks["sig-pafs[NGA]"].template
    assert tasks["sig-mid.parquet"].template is not tasks["sig-pafs[ETH]"].template


@requires_jobmon
def test_two_families_with_the_same_jobmon_name_share_one_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries of the families 'a b' and 'a/b', which jobmon_name maps to the same name, are created from one task template."""
    tools = _record_tools(monkeypatch)

    tasks = _bind([_entry("a b"), _entry("a/b")]).tasks

    assert len(_requested_templates(tools)) == 1, _requested_templates(tools)
    assert tasks["sig-a b"].template is tasks["sig-a/b"].template


@requires_jobmon
def test_bind_creates_each_task_with_its_command_upstreams_resources_and_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each created task gets its entry's command unchanged, the Jobmon tasks of its upstreams, its entry's resources as compute_resources, and max_attempts."""
    _record_tools(monkeypatch)
    plan = [_entry("a", resources={"memory": 2}), _entry("b", "a", resources={"cores": 4})]

    tasks = _bind(plan, max_attempts=3).tasks

    for entry in plan:
        task = tasks[entry.signature]
        assert task.command == entry.command
        assert task.upstream_tasks == [tasks[upstream] for upstream in entry.upstreams]
        assert task.kwargs["compute_resources"] == dict(entry.resources)
        assert task.kwargs["max_attempts"] == 3


@requires_jobmon
def test_bind_passes_resource_scales_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With resource_scales None no task is created with a resource_scales argument; with {'memory': 0.25} every task is."""
    _record_tools(monkeypatch)
    plan = [_entry("a"), _entry("b", "a")]

    unscaled = _bind(plan, resource_scales=None).tasks.values()
    scaled = _bind(plan, resource_scales={"memory": 0.25}).tasks.values()

    assert ["resource_scales" in task.kwargs for task in unscaled] == [False, False]
    assert [task.kwargs.get("resource_scales") for task in scaled] == [{"memory": 0.25}] * 2


@requires_jobmon
def test_bind_sets_the_workflows_default_resources_for_the_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workflow is created under TOOL_NAME, named workflow_name, with default_cluster_name equal to cluster and default_compute_resources_set equal to {cluster: default_resources}."""
    tools = _record_tools(monkeypatch)
    resources = {"queue": "null.q", "cores": 1}

    workflow = _bind(
        [_entry("a")],
        cluster="multiprocess",
        default_resources=resources,
        workflow_name="my_pipeline",
    ).workflow

    [tool] = [made for made in tools if workflow in made.workflows]
    assert tool.name == TOOL_NAME
    assert workflow.name == "my_pipeline"
    assert workflow.kwargs["default_cluster_name"] == "multiprocess"
    assert workflow.kwargs["default_compute_resources_set"] == {"multiprocess": resources}


@requires_jobmon
def test_two_binds_use_different_workflow_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """Binding the same plan twice creates two workflows with different workflow_args."""
    _record_tools(monkeypatch)
    plan = [_entry("a")]

    first, second = _bind(plan).workflow, _bind(plan).workflow

    assert first.workflow_args
    assert second.workflow_args
    assert first.workflow_args != second.workflow_args


@requires_jobmon
def test_bind_returns_the_workflow_id_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bind binds the workflow, returns a JobmonRun with the bound workflow's id, the plan, and a task per signature, and never calls the workflow's run."""
    _record_tools(monkeypatch)
    plan = [_entry("a"), _entry("b", "a")]

    jobmon_run = _bind(plan)

    assert jobmon_run.workflow.bound
    assert jobmon_run.workflow.runs == 0
    assert jobmon_run.workflow_id == _WORKFLOW_ID
    assert jobmon_run.plan == tuple(plan)
    assert set(jobmon_run.tasks) == {entry.signature for entry in plan}
    assert jobmon_run.status is None


@requires_jobmon
def test_bind_raises_key_error_for_an_upstream_that_is_not_an_earlier_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bind raises KeyError, not JobmonBindError, when an entry names as upstream a signature that only a later entry has, and it does so before it makes a Jobmon tool."""
    tools = _record_tools(monkeypatch)

    with pytest.raises(KeyError):
        _bind([_entry("b", "a"), _entry("a")])

    assert tools == []


@requires_jobmon
@pytest.mark.parametrize(
    "owner, method",
    [
        (_FakeTool, "create_workflow"),
        (_FakeTemplate, "create_task"),
        (_FakeWorkflow, "bind"),
    ],
    ids=["create_workflow", "create_task", "bind"],
)
@pytest.mark.parametrize(
    "error_type", [ValueError, _InvalidRequest], ids=["ValueError", "InvalidRequest"]
)
def test_bind_raises_jobmon_bind_error_when_a_jobmon_call_raises(
    owner: type[object],
    method: str,
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the recording Tool's create_workflow, a template's create_task, or the workflow's bind raises a ValueError or another Exception (such as a stand-in for jobmon.core.exceptions.InvalidRequest), bind raises JobmonBindError whose message is '<type name>: <message>' of that exception and whose __cause__ is that exception."""
    _record_tools(monkeypatch)
    message = f"Jobmon refused {method}, on purpose."
    error = error_type(message)
    monkeypatch.setattr(owner, method, _raising(error))

    with pytest.raises(JobmonBindError) as info:
        _bind([_entry("a"), _entry("b", "a")])

    assert str(info.value) == f"{error_type.__name__}: {message}"
    assert info.value.__cause__ is error


@requires_jobmon
def test_bind_lets_a_keyboard_interrupt_from_jobmon_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the recording workflow's bind raises KeyboardInterrupt, bind re-raises that KeyboardInterrupt and not a JobmonBindError."""
    _record_tools(monkeypatch)
    interrupt = KeyboardInterrupt()
    monkeypatch.setattr(_FakeWorkflow, "bind", _raising(interrupt))

    with pytest.raises(KeyboardInterrupt) as info:
        _bind([_entry("a"), _entry("b", "a")])

    assert info.value is interrupt
