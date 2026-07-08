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
    client.create_task.side_effect = lambda *a, **k: mock.MagicMock(name=k["name"])
    build_commands = {"Pakistan_artifact": "build pak", "Nigeria_artifact": "build nga"}

    status, url = artifact.build_artifacts_in_parallel(
        workflow_name="make_artifacts_x",
        build_commands=build_commands,
        native_specification=mock.MagicMock(),
        worker_logging_root=Path("/logs"),
        env_prefix="/env",
        max_concurrently_running=2,
    )

    # One task per location...
    assert client.create_task.call_count == 2
    assert {c.kwargs["name"] for c in client.create_task.call_args_list} == set(
        build_commands
    )
    assert {c.kwargs["command"] for c in client.create_task.call_args_list} == set(
        build_commands.values()
    )
    # ...with no dependencies between them (so they build in parallel)...
    client.add_upstream.assert_not_called()
    # ...all allowed to run at once, in a single workflow.
    assert client.make_workflow.call_args.kwargs["max_concurrently_running"] == 2
    client.add_tasks.assert_called_once()
    assert len(client.add_tasks.call_args.args[1]) == 2
    assert (status, url) == ("D", "http://monitor")
    assert client.bind_and_run_workflow.call_args.kwargs["resume"] is False


@mock.patch(_CLIENT)
def test_raises_when_workflow_does_not_complete(client: mock.MagicMock) -> None:
    client.JOBMON_STATUS_DONE = "D"
    client.bind_and_run_workflow.return_value = ("F", None)
    client.count_completed_tasks.return_value = 1

    with pytest.raises(RuntimeError, match="Artifact workflow .* finished with status"):
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
