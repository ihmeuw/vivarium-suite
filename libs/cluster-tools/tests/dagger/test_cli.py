"""Tests for the ``dagger`` CLI surface.

Each test invokes the click group with a mocked ``run_workflow`` so the
suite exercises only the CLI-parsing and config-loading layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("jobmon")

import yaml
from click.testing import CliRunner

from vivarium_cluster_tools.dagger.cli import dagger

_WORKFLOW_MAIN = "vivarium_cluster_tools.dagger.cli.runner.run_workflow"
_RESTART_MAIN = "vivarium_cluster_tools.dagger.cli.runner.restart_workflow"


def _write_yaml(tmp_path: Path, data: dict[str, Any], name: str = "pipeline.yaml") -> Path:
    """Write *data* as YAML to *tmp_path/name* and return the path."""
    p = tmp_path / name
    p.write_text(yaml.dump(data))
    return p


def _make_workflow_dict(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """Build a minimal valid workflow dict, applying *overrides* at the top level."""
    workflow: dict[str, Any] = {
        "name": "test_workflow",
        "project": "proj_simscience",
        "queue": "all.q",
        "output_directory": str(tmp_path / "output"),
        "steps": [
            {
                "name": "test_step",
                "command": "echo test",
                "resources": {"memory_gb": 4, "runtime": "01:00:00"},
            }
        ],
    }
    workflow.update(overrides)
    return {"workflow": workflow}


class TestDaggerRun:
    """Tests for the ``run`` subcommand's CLI surface."""

    def test_run_with_config_file(self, tmp_path: Path) -> None:
        """``dagger run`` with only ``--config`` loads the YAML and dispatches
        the parsed config to ``run_workflow``."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))

        cli_runner = CliRunner()
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = cli_runner.invoke(dagger, ["run", "--config", str(workflow_yaml)])

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["workflow_config"].name == "test_workflow"
        assert call_kwargs["workflow_config"].project == "proj_simscience"

    @pytest.mark.parametrize(
        "cli_args,expected_overrides",
        [
            (["-n", "from_cli"], {"name": "from_cli"}),
            (["--name", "from_cli"], {"name": "from_cli"}),
            (["-P", "proj_simscience_prod"], {"project": "proj_simscience_prod"}),
            (["--project", "proj_simscience_prod"], {"project": "proj_simscience_prod"}),
            (["-q", "long.q"], {"queue": "long.q"}),
            (["--queue", "long.q"], {"queue": "long.q"}),
            (["-e", "analysis_env"], {"default_environment": "analysis_env"}),
            (
                ["--default-environment", "analysis_env"],
                {"default_environment": "analysis_env"},
            ),
            (["-m", "5"], {"max_attempts": 5}),
            (["--max-attempts", "5"], {"max_attempts": 5}),
            (
                ["-P", "proj_simscience_prod", "-q", "long.q", "-n", "renamed"],
                {
                    "project": "proj_simscience_prod",
                    "queue": "long.q",
                    "name": "renamed",
                },
            ),
        ],
    )
    def test_cli_overrides_config(
        self,
        tmp_path: Path,
        cli_args: list[str],
        expected_overrides: dict[str, Any],
    ) -> None:
        """CLI flags override their corresponding YAML values, individually and
        in combination."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))

        cli_runner = CliRunner()
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = cli_runner.invoke(
                dagger, ["run", "--config", str(workflow_yaml)] + cli_args
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_main.call_args.kwargs
        for field, expected_value in expected_overrides.items():
            assert getattr(call_kwargs["workflow_config"], field) == expected_value

    def test_output_directory_override(self, tmp_path: Path) -> None:
        """``-o/--output-directory`` overrides the YAML value and is resolved
        to an absolute path."""
        original = tmp_path / "output_original"
        override = tmp_path / "output_override"
        override.mkdir()

        workflow_yaml = _write_yaml(
            tmp_path, _make_workflow_dict(tmp_path, output_directory=str(original))
        )

        cli_runner = CliRunner()
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = cli_runner.invoke(
                dagger,
                ["run", "--config", str(workflow_yaml), "-o", str(override)],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["workflow_config"].output_directory == override.resolve()

    def test_missing_config_flag_errors(self) -> None:
        """``dagger run`` without ``--config`` raises a Click usage error."""
        cli_runner = CliRunner()
        result = cli_runner.invoke(dagger, ["run"])
        assert result.exit_code != 0
        assert "missing" in result.output.lower() or "required" in result.output.lower()

    def test_nonexistent_config_path_errors(self, tmp_path: Path) -> None:
        """``--config`` with a path that doesn't exist is rejected by Click."""
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            dagger, ["run", "--config", str(tmp_path / "does_not_exist.yaml")]
        )
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower()

    @pytest.mark.parametrize(
        "missing_field, error_substring",
        [
            ("name", "name"),
            ("project", "project"),
            ("queue", "queue"),
            ("output_directory", "output"),
        ],
    )
    def test_required_field_missing_everywhere_errors(
        self, tmp_path: Path, missing_field: str, error_substring: str
    ) -> None:
        """Each of name/project/queue/output_directory must be resolvable from
        either YAML or CLI; absence from both is rejected at load time."""
        data = _make_workflow_dict(tmp_path)
        del data["workflow"][missing_field]
        workflow_yaml = _write_yaml(tmp_path, data)

        cli_runner = CliRunner()
        result = cli_runner.invoke(dagger, ["run", "--config", str(workflow_yaml)])
        assert result.exit_code != 0
        error_message = str(result.exception).lower()
        assert error_substring in error_message
        assert "required" in error_message

    @pytest.mark.parametrize(
        "missing_field, cli_flag, cli_value",
        [
            ("name", "--name", "from_cli"),
            ("project", "--project", "proj_simscience"),
            ("queue", "--queue", "all.q"),
        ],
    )
    def test_cli_fills_missing_yaml_field(
        self,
        tmp_path: Path,
        missing_field: str,
        cli_flag: str,
        cli_value: str,
    ) -> None:
        """A CLI flag satisfies the required-field check when the YAML omits
        the value entirely."""
        data = _make_workflow_dict(tmp_path)
        del data["workflow"][missing_field]
        workflow_yaml = _write_yaml(tmp_path, data)

        cli_runner = CliRunner()
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = cli_runner.invoke(
                dagger,
                ["run", "--config", str(workflow_yaml), cli_flag, cli_value],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_main.call_args.kwargs
        assert getattr(call_kwargs["workflow_config"], missing_field) == cli_value


class TestDaggerRestart:
    """Tests for the ``restart`` subcommand's CLI surface (Phase 1: xfail)."""

    def test_restart_dispatches_results_directory(self, tmp_path: Path) -> None:
        """``dagger restart <dir>`` calls ``runner.restart_workflow`` with the resolved dir."""
        results = tmp_path / "results"
        results.mkdir()
        with patch(_RESTART_MAIN) as mock_main:
            result = CliRunner().invoke(dagger, ["restart", str(results)])
        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["results_directory"] == results.resolve()

    def test_restart_requires_results_directory(self) -> None:
        """``dagger restart`` with no positional is a usage error."""
        result = CliRunner().invoke(dagger, ["restart"])
        assert result.exit_code != 0
        assert "missing" in result.output.lower() or "required" in result.output.lower()

    def test_restart_nonexistent_directory_errors(self, tmp_path: Path) -> None:
        """``dagger restart <missing>`` is rejected by Click (exists=True)."""
        result = CliRunner().invoke(dagger, ["restart", str(tmp_path / "nope")])
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower()

    def test_restart_passes_overrides(self, tmp_path: Path) -> None:
        """``dagger restart <dir> -P proj -q long.q -m 5`` forwards overrides to restart_workflow."""
        results = tmp_path / "results"
        results.mkdir()
        with patch(_RESTART_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                ["restart", str(results), "-P", "proj_x", "-q", "long.q", "-m", "5"],
            )
        assert result.exit_code == 0, result.output
        kw = mock_main.call_args.kwargs
        assert (kw["project"], kw["queue"], kw["max_attempts"]) == ("proj_x", "long.q", 5)


class TestDaggerSlackOptions:
    """``--slack-channel``/``--slack-tag`` wiring on the dagger subcommands."""

    def test_run_forwards_slack_options(self, tmp_path: Path) -> None:
        """``dagger run`` forwards --slack-channel/--slack-tag to run_workflow."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                [
                    "run",
                    "--config",
                    str(workflow_yaml),
                    "--slack-channel",
                    "my-channel",
                    "--slack-tag",
                    "coworker",
                ],
            )
        assert result.exit_code == 0, result.output
        kw = mock_main.call_args.kwargs
        assert kw["slack_channel"] == "my-channel"
        assert kw["slack_tag"] == "coworker"

    def test_restart_forwards_slack_options(self, tmp_path: Path) -> None:
        """``dagger restart`` forwards --slack-channel/--slack-tag to restart_workflow."""
        results = tmp_path / "results"
        results.mkdir()
        with patch(_RESTART_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                [
                    "restart",
                    str(results),
                    "--slack-channel",
                    "my-channel",
                    "--slack-tag",
                    "coworker",
                ],
            )
        assert result.exit_code == 0, result.output
        kw = mock_main.call_args.kwargs
        assert kw["slack_channel"] == "my-channel"
        assert kw["slack_tag"] == "coworker"

    def test_run_no_slack_mutes_notification(self, tmp_path: Path) -> None:
        """``dagger run --no-slack`` forwards mute_slack=True to run_workflow."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                ["run", "--config", str(workflow_yaml), "--no-slack"],
            )
        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["mute_slack"] is True

    def test_restart_no_slack_mutes_notification(self, tmp_path: Path) -> None:
        """``dagger restart --no-slack`` forwards mute_slack=True to restart_workflow."""
        results = tmp_path / "results"
        results.mkdir()
        with patch(_RESTART_MAIN) as mock_main:
            result = CliRunner().invoke(dagger, ["restart", str(results), "--no-slack"])
        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["mute_slack"] is True

    def test_run_no_slack_with_channel_errors(self, tmp_path: Path) -> None:
        """``--no-slack`` combined with ``--slack-channel`` is a usage error."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                [
                    "run",
                    "--config",
                    str(workflow_yaml),
                    "--no-slack",
                    "--slack-channel",
                    "my-channel",
                ],
            )
        assert result.exit_code != 0
        assert "--no-slack cannot be combined" in result.output
        mock_main.assert_not_called()

    def test_run_slack_tag_without_channel_errors(self, tmp_path: Path) -> None:
        """``--slack-tag`` without ``--slack-channel`` is a usage error."""
        workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))
        with patch(_WORKFLOW_MAIN) as mock_main:
            result = CliRunner().invoke(
                dagger,
                ["run", "--config", str(workflow_yaml), "--slack-tag", "coworker"],
            )
        assert result.exit_code != 0
        assert "--slack-tag requires --slack-channel" in result.output
        mock_main.assert_not_called()
