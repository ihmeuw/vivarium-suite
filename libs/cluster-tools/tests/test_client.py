"""Unit tests for the Jobmon client-facade helpers."""

from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("jobmon")

from vivarium.cluster_tools.core.jobmon import client


def test_make_single_command_template_builds_env_prefixed_command() -> None:
    tool = mock.MagicMock()

    template = client.make_single_command_template(tool, template_name="build_artifact")

    kwargs = tool.get_task_template.call_args.kwargs
    assert kwargs["template_name"] == "build_artifact"
    assert kwargs["command_template"] == "PATH={env_prefix}/bin:$PATH {command}"
    assert kwargs["node_args"] == ["command", "env_prefix"]
    assert kwargs["task_args"] == []
    assert kwargs["op_args"] == []
    assert template is tool.get_task_template.return_value


def test_get_incomplete_task_names_returns_only_non_done_task_names() -> None:
    done = mock.MagicMock(final_status=client.JOBMON_STATUS_DONE)
    done.name = "Pakistan_artifact"
    failed = mock.MagicMock(final_status="F")
    failed.name = "Nigeria_artifact"
    workflow = mock.MagicMock(tasks={"1": done, "2": failed})

    assert client.get_incomplete_task_names(workflow) == ["Nigeria_artifact"]
