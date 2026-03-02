import socket
from pathlib import Path
from typing import Any

import pytest

from vivarium_cluster_tools.psimulate.cluster import validate_cluster_environment
from vivarium_cluster_tools.psimulate.cluster.interface import NativeSpecification


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
    def cluster_logging_root(self, tmp_path: Path) -> Path:
        return tmp_path / "cluster_logs"

    def test_to_jobmon_spec_basic_keys(self, cluster_logging_root: Path) -> None:
        """Returned dict has all mandatory Jobmon resource keys."""
        ns = NativeSpecification(
            job_name="test_job",
            project="proj_sim_science",
            queue="all.q",
            peak_memory=4.0,
            max_runtime="01:00:00",
            hardware=["r650"],
        )
        spec = ns.to_jobmon_spec(cluster_logging_root)

        assert spec["queue"] == "all.q"
        assert spec["project"] == "proj_sim_science"
        assert spec["memory"] == 4.0
        assert spec["runtime"] == 3600
        assert spec["cores"] == ns.NUM_THREADS
        assert spec["standard_output"] == str(cluster_logging_root)
        assert spec["standard_error"] == str(cluster_logging_root)

    @pytest.mark.parametrize(
        "hardware, expected_constraints",
        [
            (["r650"], "r650"),
            (["r650", "r650v2"], "r650|r650v2"),
            (["a100", "h100", "l40s"], "a100|h100|l40s"),
            ([], None),
        ],
        ids=["single", "two", "three", "empty"],
    )
    def test_to_jobmon_spec_hardware_constraints(
        self,
        cluster_logging_root: Path,
        hardware: list[str],
        expected_constraints: str | None,
    ) -> None:
        """Hardware list is pipe-joined into 'constraints'; empty list omits the key."""
        ns = NativeSpecification(
            job_name="j",
            project="p",
            queue="q",
            peak_memory=2.0,
            max_runtime="00:30:00",
            hardware=hardware,
        )
        spec = ns.to_jobmon_spec(cluster_logging_root)
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
