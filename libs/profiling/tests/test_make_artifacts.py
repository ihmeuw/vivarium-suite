import shlex
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

import vivarium
from vivarium.profiling.constants import metadata
from vivarium.profiling.tools import make_artifacts
from vivarium.profiling.utilities import sanitize_location

try:
    from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
except ImportError:
    HAS_CLUSTER_TOOLS = False
else:
    HAS_CLUSTER_TOOLS = True

LOCATIONS = ["Pakistan", "South Africa"]
INTERFACE_MODULE = "vivarium.cluster_tools.core.cluster.interface"
ARTIFACT_MODULE = "vivarium.cluster_tools.core.jobmon.artifact"


def _make_dirs(path: Path, parents: bool = False, exists_ok: bool = False) -> None:
    """Stand in for ``vivarium.cluster_tools.mkdir``."""
    Path(path).mkdir(parents=parents, exist_ok=exists_ok)


@pytest.fixture
def build_in_parallel(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Stand in for the cluster-tools modules and return the parallel build mock."""
    cluster_tools = ModuleType("vivarium.cluster_tools")
    setattr(cluster_tools, "mkdir", _make_dirs)
    interface = ModuleType(INTERFACE_MODULE)
    setattr(interface, "NativeSpecification", SimpleNamespace)
    artifact = ModuleType(ARTIFACT_MODULE)
    build = Mock(return_value=("D", None))
    setattr(artifact, "build_artifacts_in_parallel", build)

    monkeypatch.setitem(sys.modules, "vivarium.cluster_tools", cluster_tools)
    monkeypatch.setattr(vivarium, "cluster_tools", cluster_tools, raising=False)
    monkeypatch.setitem(sys.modules, INTERFACE_MODULE, interface)
    monkeypatch.setitem(sys.modules, ARTIFACT_MODULE, artifact)
    monkeypatch.setattr(metadata, "LOCATIONS", LOCATIONS)
    return build


def test_build_all_artifacts_submits_one_command_per_location(
    build_in_parallel: Mock, tmp_path: Path
) -> None:
    make_artifacts.build_all_artifacts(tmp_path, "2021", verbose=0)

    build_in_parallel.assert_called_once()
    kwargs = build_in_parallel.call_args.kwargs
    commands = kwargs["build_commands"]
    assert list(commands) == [f"{sanitize_location(loc)}_artifact" for loc in LOCATIONS]
    this_module = str(Path(make_artifacts.__file__).resolve())
    for location, command in zip(LOCATIONS, commands.values()):
        artifact_path = tmp_path / f"{sanitize_location(location)}.hdf"
        assert shlex.split(command) == [
            sys.executable,
            this_module,
            str(artifact_path),
            location,
            "2021",
        ]

    assert kwargs["workflow_name"].startswith("make_artifacts_")
    assert kwargs["resume"] is False
    assert kwargs["env_prefix"] == sys.prefix
    assert kwargs["worker_logging_root"] == tmp_path / "logs"
    assert (tmp_path / "logs").is_dir()
    assert kwargs["max_concurrently_running"] == len(LOCATIONS)


def test_build_all_artifacts_requests_metadata_resources(
    build_in_parallel: Mock, tmp_path: Path
) -> None:
    make_artifacts.build_all_artifacts(tmp_path, None, verbose=0)

    spec = build_in_parallel.call_args.kwargs["native_specification"]
    assert spec.project == metadata.CLUSTER_PROJECT
    assert spec.queue == metadata.CLUSTER_QUEUE
    assert spec.peak_memory == metadata.MAKE_ARTIFACT_MEM
    assert spec.max_runtime == metadata.MAKE_ARTIFACT_RUNTIME
    assert spec.cores == metadata.MAKE_ARTIFACT_CPU
    assert spec.requires_archive_node is True


@pytest.mark.skipif(not HAS_CLUSTER_TOOLS, reason="vivarium-cluster-tools is not installed")
def test_resource_request_is_valid_for_cluster_tools(
    build_in_parallel: Mock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys.modules[INTERFACE_MODULE], "NativeSpecification", NativeSpecification
    )

    make_artifacts.build_all_artifacts(tmp_path, None, verbose=0)

    spec = build_in_parallel.call_args.kwargs["native_specification"]
    resources = spec.to_jobmon_spec(tmp_path / "logs")
    assert resources["memory"] == metadata.MAKE_ARTIFACT_MEM
    assert resources["cores"] == metadata.MAKE_ARTIFACT_CPU
    assert resources["constraints"] == "archive"


@pytest.mark.parametrize("years", [None, "2021", "all"])
def test_build_command_round_trips_years_through_main(
    build_in_parallel: Mock, mocker: MockerFixture, tmp_path: Path, years: str | None
) -> None:
    make_artifacts.build_all_artifacts(tmp_path, years, verbose=0)
    command = next(iter(build_in_parallel.call_args.kwargs["build_commands"].values()))
    build_one = mocker.patch.object(make_artifacts, "build_single_location_artifact")

    make_artifacts._main(shlex.split(command)[1:])

    build_one.assert_called_once_with(
        path=str(tmp_path / f"{sanitize_location(LOCATIONS[0])}.hdf"),
        location=LOCATIONS[0],
        years=years,
        log_to_file=True,
    )


@pytest.mark.parametrize("years_arg, years", [("None", None), ("2021", "2021")])
def test_main_parses_years(mocker: MockerFixture, years_arg: str, years: str | None) -> None:
    build_one = mocker.patch.object(make_artifacts, "build_single_location_artifact")

    make_artifacts._main(
        ["make_artifacts.py", "/artifacts/pakistan.hdf", "Pakistan", years_arg]
    )

    build_one.assert_called_once_with(
        path="/artifacts/pakistan.hdf", location="Pakistan", years=years, log_to_file=True
    )


def test_serial_build_passes_years_to_every_location(
    build_in_parallel: Mock, mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.object(make_artifacts, "running_from_cluster", return_value=False)
    build_single = mocker.patch.object(make_artifacts, "build_single")

    make_artifacts.build_artifacts("all", "2021", str(tmp_path), False, (), 0)

    assert build_single.call_args_list == [
        mocker.call(loc, "2021", tmp_path, ()) for loc in LOCATIONS
    ]
    build_in_parallel.assert_not_called()


def test_cluster_build_passes_years(
    build_in_parallel: Mock, mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.object(make_artifacts, "running_from_cluster", return_value=True)
    build_all = mocker.patch.object(make_artifacts, "build_all_artifacts")

    make_artifacts.build_artifacts("all", "2021", str(tmp_path), False, (), 1)

    build_all.assert_called_once_with(tmp_path, "2021", 1)
