from pathlib import Path

import pytest
import yaml

from vivarium.cluster_tools.psimulate import COMMANDS
from vivarium.cluster_tools.psimulate.paths import build_perf_log_filename, resolve_run_root

TASK_ID = "0123456789abcdef"
LAUNCH_TIME = "2031_01_01_00_00_00"
RESULT_DIRECTORY = Path("/results")


def test_build_perf_log_filename_prefixes_slurm_array_id() -> None:
    assert build_perf_log_filename(TASK_ID, "525", "3") == f"perf.525_3.{TASK_ID}.log"


def test_build_perf_log_filename_falls_back_without_array_ids() -> None:
    assert build_perf_log_filename(TASK_ID) == f"perf.{TASK_ID}.log"


def test_build_perf_log_filename_ignores_partial_array_ids() -> None:
    """One id without the other yields the legacy name, never a malformed ``525_.`` prefix."""
    assert build_perf_log_filename(TASK_ID, "525", "") == f"perf.{TASK_ID}.log"
    assert build_perf_log_filename(TASK_ID, "", "3") == f"perf.{TASK_ID}.log"


@pytest.fixture()
def model_spec(tmp_path: Path) -> Path:
    """A model specification naming no artifact of its own."""
    path = tmp_path / "kenya.yaml"
    path.write_text(yaml.dump({"configuration": {"input_data": {}}}))
    return path


class TestResolveRunRoot:
    """Verify which commands derive a run root and which are handed one."""

    def test_run_derives_a_model_named_timestamped_root(self, model_spec: Path) -> None:
        assert (
            resolve_run_root(
                command=COMMANDS.run,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=None,
                input_model_spec_path=model_spec,
                launch_time=LAUNCH_TIME,
            )
            == RESULT_DIRECTORY / "kenya" / LAUNCH_TIME
        )

    def test_run_prefers_the_artifact_name_over_the_model_spec(
        self, model_spec: Path
    ) -> None:
        """The artifact names the run when one is given, so per-location
        artifact runs land in per-location directories."""
        assert (
            resolve_run_root(
                command=COMMANDS.run,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=Path("/data/artifacts/ethiopia.hdf"),
                input_model_spec_path=model_spec,
                launch_time=LAUNCH_TIME,
            )
            == RESULT_DIRECTORY / "ethiopia" / LAUNCH_TIME
        )

    def test_run_uses_the_artifact_named_in_the_model_spec(self, tmp_path: Path) -> None:
        """A spec carrying its own artifact path names the run after it."""
        path = tmp_path / "model_spec.yaml"
        path.write_text(
            yaml.dump(
                {"configuration": {"input_data": {"artifact_path": "/data/nigeria.hdf"}}}
            )
        )
        assert (
            resolve_run_root(
                command=COMMANDS.run,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=None,
                input_model_spec_path=path,
                launch_time=LAUNCH_TIME,
            )
            == RESULT_DIRECTORY / "nigeria" / LAUNCH_TIME
        )

    def test_load_test_derives_its_own_timestamped_root(self) -> None:
        assert (
            resolve_run_root(
                command=COMMANDS.load_test,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=None,
                input_model_spec_path=None,
                launch_time=LAUNCH_TIME,
            )
            == RESULT_DIRECTORY / "load_test" / LAUNCH_TIME
        )

    @pytest.mark.parametrize("command", [COMMANDS.restart, COMMANDS.expand])
    def test_resume_commands_are_handed_the_root(self, command: str) -> None:
        """A restart or expand receives an already-resolved run root, so the
        launch time names only its logs, never a further subdirectory."""
        assert (
            resolve_run_root(
                command=command,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=None,
                input_model_spec_path=None,
                launch_time=LAUNCH_TIME,
            )
            == RESULT_DIRECTORY
        )

    def test_run_without_a_model_spec_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Model specification path must be provided"):
            resolve_run_root(
                command=COMMANDS.run,
                result_directory=RESULT_DIRECTORY,
                input_artifact_path=None,
                input_model_spec_path=None,
                launch_time=LAUNCH_TIME,
            )
