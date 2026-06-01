"""Tests for the results writing module."""

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tests.psimulate.conftest import make_job_parameters
from vivarium_cluster_tools.psimulate.results.writing import (
    collect_metadata,
    write_metadata,
    write_task_results,
)


class TestWriteMetadata:
    def test_creates_json_file(self, metadata_dir: Path) -> None:
        """write_metadata creates a JSON file named after the task_id."""
        job_params = make_job_parameters(
            input_draw=3, random_seed=7, branch_configuration={"scenario": "baseline"}
        )
        write_metadata(metadata_dir, job_params)

        expected_path = metadata_dir / f"{job_params.task_id}.json"
        assert expected_path.exists()

        with open(expected_path) as f:
            spec = json.load(f)

        assert spec["model_specification"] == "test_model_spec.yaml"
        assert spec["input_draw"] == 3
        assert spec["random_seed"] == 7
        assert spec["branch_configuration"] == {"scenario": "baseline"}
        assert spec["results_path"] == "~/tmp"

    def test_different_tasks_different_files(self, metadata_dir: Path) -> None:
        """Different job parameters produce different metadata files."""
        params_a = make_job_parameters(input_draw=0, random_seed=0)
        params_b = make_job_parameters(input_draw=1, random_seed=0)
        write_metadata(metadata_dir, params_a)
        write_metadata(metadata_dir, params_b)

        json_files = list(metadata_dir.glob("*.json"))
        assert len(json_files) == 2


class TestWriteTaskResults:
    def test_writes_metric_parquets(self, results_dir: Path) -> None:
        """Write task results and verify parquet files are created correctly."""
        for i in range(3):
            job_params = make_job_parameters(input_draw=i, random_seed=i * 10)
            results_dict = {
                "deaths": pd.DataFrame({"value": [i * 100], "year": [2020]}),
            }
            write_task_results(results_dir, job_params, results_dict)

        # pd.read_parquet on the directory combines all files
        all_deaths = pd.read_parquet(results_dir / "deaths")
        assert len(all_deaths) == 3
        assert set(all_deaths["value"].tolist()) == {0, 100, 200}
        assert set(all_deaths["input_draw"].tolist()) == {0, 1, 2}
        assert set(all_deaths["random_seed"].tolist()) == {0, 10, 20}


class TestCollectMetadata:
    def test_no_results(self, metadata_dir: Path, results_dir: Path) -> None:
        """No completed tasks means empty DataFrame."""
        result = collect_metadata(metadata_dir, results_dir)
        assert result.empty

    def test_collects_metadata_for_completed_tasks(
        self, metadata_dir: Path, results_dir: Path
    ) -> None:
        """collect_metadata reads metadata JSONs only for tasks with result parquets."""
        # Create metadata JSONs for 3 tasks
        all_params = []
        for i in range(3):
            params = make_job_parameters(input_draw=i, random_seed=i * 10)
            write_metadata(metadata_dir, params)
            all_params.append(params)

        # Only tasks 0 and 2 have result parquets
        metric_dir = results_dir / "deaths"
        metric_dir.mkdir(parents=True)
        pd.DataFrame({"value": [1]}).to_parquet(
            metric_dir / f"{all_params[0].task_id}.parquet"
        )
        pd.DataFrame({"value": [2]}).to_parquet(
            metric_dir / f"{all_params[2].task_id}.parquet"
        )

        result = collect_metadata(metadata_dir, results_dir)
        assert len(result) == 2
        assert set(result["input_draw"].tolist()) == {0, 2}

    def test_includes_branch_config(self, metadata_dir: Path, results_dir: Path) -> None:
        """Metadata DataFrame includes flattened branch configuration keys."""
        params = make_job_parameters(
            input_draw=5,
            random_seed=99,
            branch_configuration={"scenario": {"treatment": "A"}},
        )
        write_metadata(metadata_dir, params)

        metric_dir = results_dir / "metric"
        metric_dir.mkdir(parents=True)
        pd.DataFrame({"v": [1]}).to_parquet(metric_dir / f"{params.task_id}.parquet")

        result = collect_metadata(metadata_dir, results_dir)
        assert len(result) == 1
        assert result["input_draw"].iloc[0] == 5
        assert result["random_seed"].iloc[0] == 99
        assert result["scenario.treatment"].iloc[0] == "A"

    def test_ignores_tasks_without_metadata(
        self, metadata_dir: Path, results_dir: Path
    ) -> None:
        """Tasks with parquets but no metadata JSON are silently skipped."""
        metric_dir = results_dir / "metric"
        metric_dir.mkdir(parents=True)
        pd.DataFrame({"v": [1]}).to_parquet(metric_dir / "orphan_task.parquet")

        result = collect_metadata(metadata_dir, results_dir)
        assert result.empty
