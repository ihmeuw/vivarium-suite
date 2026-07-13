"""Unit tests for the Jobmon client-facade helpers."""

from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("jobmon")

from vivarium.cluster_tools.core.jobmon import client


def test_get_incomplete_task_names_returns_only_non_done_task_names() -> None:
    done = mock.MagicMock(final_status=client.JOBMON_STATUS_DONE)
    done.name = "Pakistan_artifact"
    failed = mock.MagicMock(final_status="F")
    failed.name = "Nigeria_artifact"
    workflow = mock.MagicMock(tasks={"1": done, "2": failed})

    assert client.get_incomplete_task_names(workflow) == ["Nigeria_artifact"]
