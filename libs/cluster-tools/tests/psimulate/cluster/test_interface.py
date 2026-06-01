import socket
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from vivarium_cluster_tools.core.cluster.interface import (
    _SLURM_TIMEOUT_BUFFER_SECONDS,
    NativeSpecification,
    _parse_slurm_time,
    get_workflow_timeout_seconds,
)
from vivarium_cluster_tools.core.cluster.validation import validate_cluster_environment


@pytest.fixture(
    params=[
        "gen-slurm-sarchive-p0051",
        "long-slurm-sarchive-p0022",
    ]
)
def good_host(request: pytest.FixtureRequest) -> str:
    # request.param can typically be Any, but for this test we need a str
    if not isinstance(request.param, str):
        raise TypeError(f"Expected str, got {type(request.param)}")
    return request.param


@pytest.fixture(
    params=[
        "gen-slurm-slogin-p01" "gen-uge-submit-p01",
        "-slogin-",
    ]
)
def bad_host(request: pytest.FixtureRequest) -> str:
    # request.param can typically be Any, but for this test we need a str
    if not isinstance(request.param, str):
        raise TypeError(f"Expected str, got {type(request.param)}")
    return request.param


def test_validate_cluster_environment_pass(
    monkeypatch: pytest.MonkeyPatch, good_host: str
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: good_host)
    validate_cluster_environment()


def test_validate_cluster_environment_fail(
    monkeypatch: pytest.MonkeyPatch, bad_host: str
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: bad_host)
    with pytest.raises(RuntimeError):
        validate_cluster_environment()


class TestNativeSpecification:
    """Tests for NativeSpecification.to_jobmon_spec and _runtime_to_seconds."""

    @pytest.fixture()
    def worker_logging_root(self, tmp_path: Path) -> Path:
        return tmp_path / "worker_logs"

    def test_to_jobmon_spec_basic_keys(self, worker_logging_root: Path) -> None:
        """Returned dict has all mandatory Jobmon resource keys."""
        ns = NativeSpecification(
            job_name="test_job",
            project="proj_sim_science",
            queue="all.q",
            peak_memory=4.0,
            max_runtime="01:00:00",
            hardware=["r650"],
        )
        spec = ns.to_jobmon_spec(worker_logging_root)

        assert spec["queue"] == "all.q"
        assert spec["project"] == "proj_sim_science"
        assert spec["memory"] == 4.0
        assert spec["runtime"] == 3600
        assert spec["cores"] == 1  # default
        assert spec["stdout"] == str(worker_logging_root)
        assert spec["stderr"] == str(worker_logging_root)

    @pytest.mark.parametrize(
        "hardware, requires_archive_node, expected_constraints",
        [
            (["r650"], False, "(r650)"),
            (["r650", "r650v2"], False, "(r650|r650v2)"),
            (["a100", "h100", "l40s"], False, "(a100|h100|l40s)"),
            ([], False, None),
            ([], True, "archive"),
            (["r650"], True, "(r650)&archive"),
            (["r650", "r650v2"], True, "(r650|r650v2)&archive"),
            (["a100", "h100", "l40s"], True, "(a100|h100|l40s)&archive"),
        ],
        ids=[
            "hw-single",
            "hw-two",
            "hw-three",
            "none",
            "archive-only",
            "hw-single-archive",
            "hw-two-archive",
            "hw-three-archive",
        ],
    )
    def test_to_jobmon_spec_hardware_constraints(
        self,
        worker_logging_root: Path,
        hardware: list[str],
        requires_archive_node: bool,
        expected_constraints: str | None,
    ) -> None:
        """Constraint string combines ``hardware`` (OR) with archive (AND)."""
        ns = NativeSpecification(
            job_name="j",
            project="p",
            queue="q",
            peak_memory=2.0,
            max_runtime="00:30:00",
            hardware=hardware,
            requires_archive_node=requires_archive_node,
        )
        spec = ns.to_jobmon_spec(worker_logging_root)
        if expected_constraints is None:
            assert "constraints" not in spec
        else:
            assert spec["constraints"] == expected_constraints

    @pytest.mark.parametrize(
        "runtime_str, expected",
        [
            ("01:00:00", 3600),
            ("00:30:00", 1800),
            ("02:15:30", 2 * 3600 + 15 * 60 + 30),
            ("00:00:00", 0),
            ("10:00:00", 36000),
            ("30:00", 1800),
            ("01:30", 90),
            ("00:00", 0),
            ("3600", 3600),
            ("0", 0),
        ],
    )
    def test_runtime_to_seconds(self, runtime_str: str, expected: int) -> None:
        assert NativeSpecification._runtime_to_seconds(runtime_str) == expected


class TestParseSlurmTime:
    """Tests for _parse_slurm_time."""

    @pytest.mark.parametrize(
        "time_str, expected",
        [
            ("10:00:00", 36000),
            ("1:30:00", 5400),
            ("00:05:00", 300),
            ("5:00", 300),
            ("59", 59),
            ("0:59", 59),
            ("1-00:00:00", 86400),
            ("2-12:00:00", 2 * 86400 + 12 * 3600),
            ("1-01:30:45", 86400 + 3600 + 30 * 60 + 45),
        ],
    )
    def test_parse_slurm_time(self, time_str: str, expected: int) -> None:
        assert _parse_slurm_time(time_str) == expected

    @pytest.mark.parametrize(
        "bad_value",
        ["UNLIMITED", "NOT_SET", "abc", "1:2:3:4", "--:--", "", "1-30", "1-10:30"],
    )
    def test_parse_slurm_time_rejects_invalid(self, bad_value: str) -> None:
        with pytest.raises(ValueError, match="Unrecognized SLURM time format"):
            _parse_slurm_time(bad_value)


class TestGetRunnerNodeRemainingSeconds:
    """Tests for get_runner_node_remaining_seconds."""

    def convert_seconds_to_time_str(self, seconds: int) -> str:
        """Helper to convert seconds to HH:MM:SS format."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @pytest.fixture(autouse=True)
    def _set_slurm_job_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set SLURM_JOB_ID for all tests; individual tests can override."""
        monkeypatch.setenv("SLURM_JOB_ID", "12345")

    def test_no_slurm_job_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return None when not inside a SLURM allocation."""
        monkeypatch.delenv("SLURM_JOB_ID")
        assert get_workflow_timeout_seconds() is None

    def test_returns_remaining_minus_buffer(self) -> None:
        """Return remaining seconds minus the safety buffer."""
        completed = _make_squeue_result("10:00:00")
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            result = get_workflow_timeout_seconds()
        assert result == 36000 - _SLURM_TIMEOUT_BUFFER_SECONDS

    def test_raises_for_unlimited(self) -> None:
        """Raise when SLURM reports UNLIMITED time."""
        completed = _make_squeue_result("UNLIMITED")
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(ValueError, match="Unrecognized SLURM time format"):
                get_workflow_timeout_seconds()

    @pytest.mark.parametrize(
        "bad_value",
        ["NOT_SET", "INVALID", "abc:def:ghi", "--:--:--", "inf"],
        ids=["NOT_SET", "INVALID", "non-numeric-colons", "dashes", "inf"],
    )
    def test_raises_for_non_time_strings(self, bad_value: str) -> None:
        """Raise when squeue returns a non-numeric time string."""
        completed = _make_squeue_result(bad_value)
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(ValueError, match="Unrecognized SLURM time format"):
                get_workflow_timeout_seconds()

    def test_raises_when_remaining_less_than_buffer(self) -> None:
        """Raise when remaining time is less than the safety buffer."""
        remaining_seconds = _SLURM_TIMEOUT_BUFFER_SECONDS - 1
        completed = _make_squeue_result(self.convert_seconds_to_time_str(remaining_seconds))
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(RuntimeError, match="Not enough time"):
                get_workflow_timeout_seconds()

    def test_raises_when_remaining_exactly_equals_buffer(self) -> None:
        """Raise when remaining time exactly equals the safety buffer."""
        # Convert the seconds buffer to HH:MM:SS format
        completed = _make_squeue_result(
            self.convert_seconds_to_time_str(_SLURM_TIMEOUT_BUFFER_SECONDS)
        )
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(RuntimeError, match="Not enough time"):
                get_workflow_timeout_seconds()

    def test_returns_one_second_when_just_above_buffer(self) -> None:
        remaining_seconds = _SLURM_TIMEOUT_BUFFER_SECONDS + 1
        completed = _make_squeue_result(self.convert_seconds_to_time_str(remaining_seconds))
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            assert get_workflow_timeout_seconds() == 1

    def test_raises_on_subprocess_error(self) -> None:
        """Raise when squeue fails."""
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            side_effect=FileNotFoundError("squeue not found"),
        ):
            with pytest.raises(RuntimeError, match="Could not determine"):
                get_workflow_timeout_seconds()

    def test_raises_on_subprocess_timeout(self) -> None:
        """Raise when squeue times out."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="squeue", timeout=10),
        ):
            with pytest.raises(RuntimeError, match="Could not determine"):
                get_workflow_timeout_seconds()

    def test_raises_on_nonzero_returncode(self) -> None:
        """Raise when squeue exits with a non-zero return code."""
        completed = subprocess.CompletedProcess(
            args=["squeue", "-h", "-j", "12345", "-o", "%L"],
            returncode=1,  # non-zero return code
            stdout="",
            stderr="slurm_load_jobs error: Invalid job id specified\n",
        )
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(RuntimeError, match="squeue failed"):
                get_workflow_timeout_seconds()

    def test_raises_on_empty_squeue_output(self) -> None:
        """Raise when squeue returns empty output."""
        completed = _make_squeue_result("")
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            with pytest.raises(RuntimeError, match="no output"):
                get_workflow_timeout_seconds()

    def test_handles_day_format(self) -> None:
        """Handle D-HH:MM:SS format from squeue."""
        completed = _make_squeue_result("1-12:00:00")
        with patch(
            "vivarium_cluster_tools.core.cluster.interface.subprocess.run",
            return_value=completed,
        ):
            result = get_workflow_timeout_seconds()
        expected = (86400 + 12 * 3600) - _SLURM_TIMEOUT_BUFFER_SECONDS
        assert result == expected


def _make_squeue_result(time_str: str) -> Any:
    """Create a mock subprocess.CompletedProcess for squeue output."""

    return subprocess.CompletedProcess(
        args=["squeue", "-h", "-j", "12345", "-o", "%L"],
        returncode=0,
        stdout=f"{time_str}\n",
        stderr="",
    )
