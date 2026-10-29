"""Tests for the bridge's pytask plugin (``bridge.plugin``)."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pluggy import PluginManager
from pytask import TaskOutcome

from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.bridge import execute
from vivarium.workflow.bridge.execute import RUNS_DIR
from vivarium.workflow.bridge.plugin import (
    DEFAULT_CLUSTER,
    DEFAULT_MAX_ATTEMPTS,
    pytask_parse_config,
    pytask_post_parse,
)
from vivarium.workflow.harness import FakeJobmon, build_fixture

_ENTRY_POINT = "vivarium_workflow_bridge"
"""Name of the bridge's entry point in the ``pytask`` group."""

_TIMEOUT = 300
"""Seconds after which a subprocess is taken to hang."""

_IMPORT_PLUGIN = """
import sys

import vivarium.workflow.bridge.plugin

loaded = sorted(
    name
    for name in sys.modules
    if name == "vivarium.workflow.bridge.execute" or name.partition(".")[0] == "jobmon"
)
assert not loaded, loaded
"""
"""Script that imports the plugin and fails if that imported the execute module or jobmon."""

_BRIDGE_KEYS = (
    "jobmon",
    "jobmon_cluster",
    "jobmon_resources",
    "jobmon_max_attempts",
    "jobmon_resource_scales",
)
"""The configuration keys that the bridge sets and reads."""


def _set_by_build() -> dict[str, Any]:
    """Return a value other than the default for each of the bridge's keys."""
    return {
        "jobmon": True,
        "jobmon_cluster": "multiprocess",
        "jobmon_resources": {"queue": "null.q", "memory": 1},
        "jobmon_max_attempts": 5,
        "jobmon_resource_scales": {"memory": 0.25, "runtime": 0.75},
    }


def _parsed_config(pm: PluginManager, **flags: bool) -> dict[str, Any]:
    """Return a parsed configuration: the bridge's defaults, every mode off, then ``flags``."""
    return {
        "pm": pm,
        "jobmon": False,
        "jobmon_cluster": DEFAULT_CLUSTER,
        "jobmon_resources": {},
        "jobmon_max_attempts": DEFAULT_MAX_ATTEMPTS,
        "jobmon_resource_scales": None,
        "dry_run": False,
        "explain": False,
        "pdb": False,
        "trace": False,
        **flags,
    }


class _RecordingPluginManager(PluginManager):
    """A pytask plugin manager that records each plugin registered with it."""

    def __init__(self) -> None:
        super().__init__("pytask")
        self.registered: list[object] = []

    def register(self, plugin: object, name: str | None = None) -> str | None:
        """Record ``plugin``, then register it."""
        self.registered.append(plugin)
        return super().register(plugin, name)


# The entry point and the module's imports


def test_the_pytask_entry_point_loads_the_plugin_module() -> None:
    """importlib.metadata.entry_points(group='pytask') has 'vivarium_workflow_bridge' pointing at vivarium.workflow.bridge.plugin, and the assertion's message tells the developer to re-run 'pip install -e .' if it is missing."""
    entry_points = importlib.metadata.entry_points(group="pytask")

    assert _ENTRY_POINT in entry_points.names, (
        f"No pytask entry point named {_ENTRY_POINT!r} is installed. Re-run "
        "'pip install -e .' in libs/workflow, so that pytask finds the bridge."
    )
    assert entry_points[_ENTRY_POINT].value == "vivarium.workflow.bridge.plugin"


def test_importing_the_plugin_imports_neither_execute_nor_jobmon() -> None:
    """In a fresh interpreter, importing vivarium.workflow.bridge.plugin leaves vivarium.workflow.bridge.execute and every jobmon module out of sys.modules."""
    imported = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PLUGIN],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )

    assert imported.returncode == 0, imported.stderr


# Configuration defaults


def test_parse_config_sets_every_default() -> None:
    """pytask_parse_config on a config without the bridge's keys sets jobmon False, jobmon_cluster DEFAULT_CLUSTER, jobmon_resources {}, jobmon_max_attempts DEFAULT_MAX_ATTEMPTS and jobmon_resource_scales None."""
    config: dict[str, Any] = {"dry_run": False}

    pytask_parse_config(config)

    parsed = {key: config.get(key, "unset") for key in _BRIDGE_KEYS}
    assert parsed == {
        "jobmon": False,
        "jobmon_cluster": DEFAULT_CLUSTER,
        "jobmon_resources": {},
        "jobmon_max_attempts": DEFAULT_MAX_ATTEMPTS,
        "jobmon_resource_scales": None,
    }
    assert config["dry_run"] is False


def test_parse_config_keeps_the_values_a_build_set() -> None:
    """pytask_parse_config leaves each bridge key that the config already holds unchanged."""
    config = _set_by_build()

    pytask_parse_config(config)

    assert config == _set_by_build()


def test_build_passes_its_bridge_options_to_the_jobmon_adapter(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A build of the harness fixture with jobmon=True and every jobmon_* key set binds one plan whose options carry those values, and workflow_name is the root directory's name."""
    fx = build_fixture(tmp_path)

    build(fx.tasks, fx.state, **_set_by_build())

    expected = {
        "cluster": "multiprocess",
        "default_resources": {"queue": "null.q", "memory": 1},
        "max_attempts": 5,
        "resource_scales": {"memory": 0.25, "runtime": 0.75},
        "workflow_name": fx.state.name,
    }
    assert len(fake_jobmon.plans) == 1
    assert fake_jobmon.options == [expected]


def test_a_build_with_only_jobmon_set_binds_with_the_defaults(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A build with jobmon=True and no other bridge key binds with cluster 'slurm', default_resources {}, max_attempts 2 and resource_scales None."""
    fx = build_fixture(tmp_path)

    build(fx.tasks, fx.state, jobmon=True)

    [options] = fake_jobmon.options
    keys = ("cluster", "default_resources", "max_attempts", "resource_scales")
    bound = {key: options[key] for key in keys}
    assert bound == {
        "cluster": "slurm",
        "default_resources": {},
        "max_attempts": 2,
        "resource_scales": None,
    }


# When the bridge takes over


def test_post_parse_registers_execute_when_jobmon_is_set() -> None:
    """pytask_post_parse, called with a config whose jobmon is True and a recording plugin manager, registers the vivarium.workflow.bridge.execute module once."""
    pm = _RecordingPluginManager()

    pytask_post_parse(_parsed_config(pm, jobmon=True))

    assert pm.registered == [execute]


@pytest.mark.parametrize("mode", ["dry_run", "explain", "pdb", "trace"])
def test_post_parse_registers_nothing_under_a_local_only_mode(mode: str) -> None:
    """With jobmon True and one of LOCAL_ONLY_MODES set, pytask_post_parse registers nothing."""
    pm = _RecordingPluginManager()

    pytask_post_parse(_parsed_config(pm, jobmon=True, **{mode: True}))

    assert pm.registered == []


def test_post_parse_registers_nothing_without_jobmon() -> None:
    """With jobmon False, pytask_post_parse registers nothing."""
    pm = _RecordingPluginManager()

    pytask_post_parse(_parsed_config(pm))

    assert pm.registered == []


def test_a_build_without_jobmon_runs_locally_and_binds_no_plan(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A build of the harness fixture without jobmon reports SUCCESS for every step from the build's own process, and FakeJobmon records no plan."""
    fx = build_fixture(tmp_path)

    session = build(fx.tasks, fx.state)

    assert outcomes(session) == dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert fake_jobmon.plans == []
    assert not (fx.state / RUNS_DIR).exists()


def test_preview_with_jobmon_stays_local(tmp_path: Path, fake_jobmon: FakeJobmon) -> None:
    """preview(tasks, root, jobmon=True) returns the same plan as preview without it, and FakeJobmon records no plan."""
    fx = build_fixture(tmp_path)

    bridged = preview(fx.tasks, fx.state, jobmon=True)
    local = preview(fx.tasks, fx.state)

    plan = [(step.name, step.outcome) for step in bridged]
    assert plan == [(step.name, step.outcome) for step in local]
    assert fake_jobmon.plans == []
    assert not any(fx.registry.path_for(step).exists() for step in fx.steps)


@pytest.mark.parametrize("mode", ["dry_run", "explain"])
def test_a_dry_run_or_explanation_with_jobmon_stays_local(
    tmp_path: Path, mode: str, fake_jobmon: FakeJobmon
) -> None:
    """A build with jobmon=True and dry_run or explain set (explain without dry_run) binds no plan, writes no product, and creates no run directory."""
    fx = build_fixture(tmp_path)

    build(fx.tasks, fx.state, jobmon=True, **{mode: True})

    assert fake_jobmon.plans == []
    assert not any(fx.registry.path_for(step).exists() for step in fx.steps)
    assert not (fx.state / RUNS_DIR).exists()
