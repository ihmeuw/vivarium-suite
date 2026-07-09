"""Unit tests for the parallel artifact-build Jobmon workflow runner."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("jobmon")

from vivarium.cluster_tools.core.jobmon import artifact

_CLIENT = "vivarium.cluster_tools.core.jobmon.artifact.client"


@mock.patch(_CLIENT)
def test_builds_one_independent_task_per_location(client: mock.MagicMock) -> None:
    client.JOBMON_STATUS_DONE = "D"
    client.bind_and_run_workflow.return_value = ("D", "http://monitor")
    client.create_task.side_effect = lambda *a, **k: mock.MagicMock()
    native_specification = mock.MagicMock()
    build_commands = {"Pakistan_artifact": "build pak", "Nigeria_artifact": "build nga"}

    status, url = artifact.build_artifacts_in_parallel(
        workflow_name="make_artifacts_x",
        build_commands=build_commands,
        native_specification=native_specification,
        worker_logging_root=Path("/logs"),
        env_prefix="/env",
        max_concurrently_running=2,
    )

    # One task per location, each with its own name and command, in the given env.
    assert client.create_task.call_count == 2
    assert {c.kwargs["name"] for c in client.create_task.call_args_list} == set(
        build_commands
    )
    assert {c.kwargs["command"] for c in client.create_task.call_args_list} == set(
        build_commands.values()
    )
    assert {c.kwargs["env_prefix"] for c in client.create_task.call_args_list} == {"/env"}

    # Tasks run the env-prefixed single-command template.
    template_kwargs = client.make_task_template.call_args.kwargs
    assert template_kwargs["template_name"] == "build_artifact"
    assert template_kwargs["command_template"] == "PATH={env_prefix}/bin:$PATH {command}"
    assert template_kwargs["node_args"] == ["command", "env_prefix"]

    # Every task gets the compute resources the native spec produced for the log root.
    native_specification.to_jobmon_spec.assert_called_once_with(Path("/logs"))
    assert all(
        c.kwargs["compute_resources"] is native_specification.to_jobmon_spec.return_value
        for c in client.create_task.call_args_list
    )

    # No inter-task dependencies (so they build in parallel), all at once, retried.
    client.add_upstream.assert_not_called()
    assert client.make_workflow.call_args.kwargs["max_concurrently_running"] == 2
    assert client.make_workflow.call_args.kwargs["max_attempts"] == 2
    client.add_tasks.assert_called_once()
    assert len(client.add_tasks.call_args.args[1]) == 2

    assert (status, url) == ("D", "http://monitor")
    assert client.bind_and_run_workflow.call_args.kwargs["resume"] is False


@mock.patch(_CLIENT)
def test_raises_and_names_unfinished_locations(client: mock.MagicMock) -> None:
    client.JOBMON_STATUS_DONE = "D"
    client.bind_and_run_workflow.return_value = ("F", None)
    client.count_completed_tasks.return_value = 1
    client.get_incomplete_task_names.return_value = ["Nigeria_artifact"]

    with pytest.raises(RuntimeError) as excinfo:
        artifact.build_artifacts_in_parallel(
            workflow_name="make_artifacts_x",
            build_commands={
                "Pakistan_artifact": "build pak",
                "Nigeria_artifact": "build nga",
            },
            native_specification=mock.MagicMock(),
            worker_logging_root=Path("/logs"),
            env_prefix="/env",
        )

    message = str(excinfo.value)
    assert "finished with status 'F': 1/2 location artifacts built" in message
    assert "Did not finish: Nigeria_artifact" in message


@mock.patch(_CLIENT)
def test_resume_is_forwarded_to_the_workflow_run(client: mock.MagicMock) -> None:
    client.JOBMON_STATUS_DONE = "D"
    client.bind_and_run_workflow.return_value = ("D", None)

    artifact.build_artifacts_in_parallel(
        workflow_name="make_artifacts_x",
        build_commands={"Pakistan_artifact": "build pak"},
        native_specification=mock.MagicMock(),
        worker_logging_root=Path("/logs"),
        env_prefix="/env",
        resume=True,
    )

    assert client.bind_and_run_workflow.call_args.kwargs["resume"] is True


@mock.patch(_CLIENT)
def test_empty_build_commands_raises_before_touching_jobmon(
    client: mock.MagicMock,
) -> None:
    with pytest.raises(ValueError, match="build_commands is empty"):
        artifact.build_artifacts_in_parallel(
            workflow_name="make_artifacts_x",
            build_commands={},
            native_specification=mock.MagicMock(),
            worker_logging_root=Path("/logs"),
            env_prefix="/env",
        )
    client.make_tool.assert_not_called()
