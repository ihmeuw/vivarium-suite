"""Tests for psimulate CLI features.
"""

import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("jobmon")

import yaml
from click.testing import CliRunner

from vivarium_cluster_tools.psimulate.cli import psimulate

# ---------------------------------------------------------------------------
# The runner.main call is the "leaf" of every CLI command.  We mock it so
# tests exercise only the CLI-parsing / config-loading layer.
# ---------------------------------------------------------------------------
_RUNNER_MAIN = "vivarium_cluster_tools.psimulate.runner.main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict[str, Any], name: str = "config.yaml") -> Path:
    """Write *data* as YAML to a temp file and return its path."""
    p = tmp_path / name
    p.write_text(yaml.dump(data))
    return p


def _make_results_root(tmp_path: Path) -> Path:
    """Create a writable directory that can serve as results_root."""
    d = tmp_path / "previous_run"
    d.mkdir()
    return d


@pytest.fixture()
def model_spec(tmp_path: Path) -> Path:
    """A minimal model specification file."""
    p = tmp_path / "model_spec.yaml"
    p.write_text("components: {}\n")
    return p


@pytest.fixture()
def branch_config(tmp_path: Path) -> Path:
    """A minimal branch configuration file."""
    p = tmp_path / "branches.yaml"
    p.write_text("input_draw_count: 1\nrandom_seed_count: 1\n")
    return p


@pytest.fixture()
def result_dir(tmp_path: Path) -> Path:
    """A temporary results directory."""
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture()
def results_root(tmp_path: Path) -> Path:
    """A writable directory that can serve as results_root."""
    return _make_results_root(tmp_path)


class TestRunSubcommand:
    """Tests for --run-config on the ``run`` subcommand."""

    def test_full_config_only(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """All required args supplied via config file — no CLI args needed."""
        config = _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "proj_simscience",
                "peak_memory": 5,
                "max_runtime": "01:00:00",
                "queue": "all.q",
                "max_workers": 100,
                "max_attempts": 2,
                "hardware": "r650",
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(psimulate, ["run", "--run-config", str(config)])

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["native_specification"].project == "proj_simscience"
        assert call_kwargs["native_specification"].peak_memory == 5
        assert call_kwargs["native_specification"].queue == "all.q"
        assert call_kwargs["max_workers"] == 100
        assert call_kwargs["max_attempts"] == 2
        assert call_kwargs["input_paths"].model_specification == model_spec.resolve()
        assert call_kwargs["input_paths"].branch_configuration == branch_config.resolve()
        assert call_kwargs["input_paths"].result_directory == result_dir.resolve()

    def test_cli_overrides_config(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """A value given on the CLI wins over the same key in the config."""
        config = _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "proj_simscience",
                "peak_memory": 3,
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "--run-config",
                    str(config),
                    "--peak-memory",
                    "10",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        # The NativeSpecification should have peak_memory=10 (CLI), not 3 (config)
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["native_specification"].peak_memory == 10

    def test_partial_config_plus_cli(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """Config provides some values, CLI provides the rest."""
        # Config only has cluster options
        config = _write_yaml(
            tmp_path,
            {
                "project": "proj_simscience",
                "peak_memory": 5,
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "--run-config",
                    str(config),
                    str(model_spec),  # positional arg on CLI
                    str(branch_config),  # positional arg on CLI
                    "--result-directory",
                    str(result_dir),
                ],
            )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        # peak_memory came from config, project came from config
        assert call_kwargs["native_specification"].peak_memory == 5
        assert call_kwargs["native_specification"].project == "proj_simscience"
        # positional args came from CLI
        assert call_kwargs["input_paths"].model_specification == model_spec.resolve()

    def test_positional_args_from_config(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """Positional arguments (model_specification, branch_configuration) can
        be provided in the config file."""
        config = _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "proj_simscience",
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            # No positional args on CLI at all
            result = cli_runner.invoke(psimulate, ["run", "--run-config", str(config)])

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()


class TestRestartSubcommand:
    """Tests for --run-config on the ``restart`` subcommand."""

    def test_full_config_only(self, tmp_path: Path, results_root: Path) -> None:
        config = _write_yaml(
            tmp_path,
            {
                "results_root": str(results_root),
                "project": "proj_simscience",
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(psimulate, ["restart", "--run-config", str(config)])

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["native_specification"].project == "proj_simscience"
        assert call_kwargs["input_paths"].result_directory == results_root.resolve()

    def test_cli_overrides_config(self, tmp_path: Path, results_root: Path) -> None:
        config = _write_yaml(
            tmp_path,
            {
                "results_root": str(results_root),
                "project": "proj_simscience",
                "peak_memory": 3,
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "restart",
                    "--run-config",
                    str(config),
                    "--peak-memory",
                    "8",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["native_specification"].peak_memory == 8


class TestExpandSubcommand:
    """Tests for --run-config on the ``expand`` subcommand."""

    def test_full_config_only(self, tmp_path: Path, results_root: Path) -> None:
        config = _write_yaml(
            tmp_path,
            {
                "results_root": str(results_root),
                "project": "proj_simscience",
                "add_draws": 5,
                "add_seeds": 3,
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(psimulate, ["expand", "--run-config", str(config)])

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["native_specification"].project == "proj_simscience"
        assert call_kwargs["input_paths"].result_directory == results_root.resolve()
        assert call_kwargs["extra_args"]["num_draws"] == 5
        assert call_kwargs["extra_args"]["num_seeds"] == 3

    def test_expand_cli_overrides_config(self, tmp_path: Path, results_root: Path) -> None:
        """add_draws and add_seeds in config are overridden by CLI."""
        config = _write_yaml(
            tmp_path,
            {
                "results_root": str(results_root),
                "project": "proj_simscience",
                "add_draws": 5,
                "add_seeds": 3,
            },
        )

        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "expand",
                    "--run-config",
                    str(config),
                    "--add-draws",
                    "10",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["extra_args"]["num_draws"] == 10  # CLI override
        assert call_kwargs["extra_args"]["num_seeds"] == 3  # from config


class TestSlackOptions:
    """``--slack-channel``/``--slack-tag`` wiring on the psimulate subcommands."""

    def test_run_forwards_slack_options(
        self, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """``psimulate run`` forwards --slack-channel/--slack-tag to runner.main."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "-M",
                    str(model_spec),
                    "-B",
                    str(branch_config),
                    "-o",
                    str(result_dir),
                    "-P",
                    "proj_simscience",
                    "--slack-channel",
                    "my-channel",
                    "--slack-tag",
                    "coworker",
                ],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["slack_channel"] == "my-channel"
        assert call_kwargs["slack_tag"] == "coworker"

    def test_run_no_slack_mutes_notification(
        self, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """``--no-slack`` forwards mute_slack=True to runner.main."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "-M",
                    str(model_spec),
                    "-B",
                    str(branch_config),
                    "-o",
                    str(result_dir),
                    "-P",
                    "proj_simscience",
                    "--no-slack",
                ],
            )

        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["mute_slack"] is True

    def test_run_slack_tag_without_channel_errors(
        self, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """``--slack-tag`` without ``--slack-channel`` is a usage error."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "-M",
                    str(model_spec),
                    "-B",
                    str(branch_config),
                    "-o",
                    str(result_dir),
                    "-P",
                    "proj_simscience",
                    "--slack-tag",
                    "coworker",
                ],
            )

        assert result.exit_code != 0
        assert "--slack-tag requires --slack-channel" in result.output
        mock_main.assert_not_called()


class TestErrorHandling:
    """Tests for error cases in --run-config."""

    def test_missing_config_file(self) -> None:
        """--run-config pointing to a nonexistent file is an error."""
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            psimulate,
            [
                "run",
                "--run-config",
                "/nonexistent/path/config.yaml",
            ],
        )
        assert result.exit_code != 0
        assert "does not exist" in result.output or "Invalid value" in result.output

    def test_unknown_key_in_config(self, tmp_path: Path) -> None:
        """Unrecognized keys in the config file produce an error."""
        config = _write_yaml(
            tmp_path,
            {
                "project": "proj_simscience",
                "bogus_option": "some_value",
            },
        )
        cli_runner = CliRunner()
        result = cli_runner.invoke(psimulate, ["run", "--run-config", str(config)])
        assert result.exit_code != 0
        assert "bogus_option" in result.output


class TestValidationParity:
    """Config file values must go through the same Click validation as CLI args."""

    def test_invalid_project_choice(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """A project value not in the Choice list is rejected."""
        config = _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "invalid_project",
            },
        )

        cli_runner = CliRunner()
        result = cli_runner.invoke(psimulate, ["run", "--run-config", str(config)])
        assert result.exit_code != 0
        assert "invalid_project" in result.output or "Invalid value" in result.output

    def test_invalid_hardware(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """An invalid hardware value is caught by the validation callback."""
        config = _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "proj_simscience",
                "hardware": "nonexistent_node_type",
            },
        )

        cli_runner = CliRunner()
        result = cli_runner.invoke(psimulate, ["run", "--run-config", str(config)])
        assert result.exit_code != 0
        assert "nonexistent_node_type" in result.output or "not supported" in result.output


# The date after which positional argument support should be removed.
_DEPRECATION_DEADLINE = datetime.date(2026, 10, 9)


class TestKeywordArgs:
    """Using the new keyword options works without deprecation warnings."""

    def test_run_keyword_args(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """model_specification and branch_configuration via --flags."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "--model-specification",
                    str(model_spec),
                    "--branch-configuration",
                    str(branch_config),
                    "--result-directory",
                    str(result_dir),
                    "--project",
                    "proj_simscience",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "deprecated" not in result.output.lower()
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["input_paths"].model_specification == model_spec.resolve()
        assert call_kwargs["input_paths"].branch_configuration == branch_config.resolve()

    def test_run_short_flags(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        """Short flags -f and -b work for model spec and branches."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "run",
                    "-M",
                    str(model_spec),
                    "-B",
                    str(branch_config),
                    "-o",
                    str(result_dir),
                    "-P",
                    "proj_simscience",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "deprecated" not in result.output.lower()
        mock_main.assert_called_once()

    def test_restart_keyword_arg(self, tmp_path: Path, results_root: Path) -> None:
        """results_root via --results-root flag."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "restart",
                    "--results-root",
                    str(results_root),
                    "--project",
                    "proj_simscience",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "deprecated" not in result.output.lower()
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["input_paths"].result_directory == results_root.resolve()

    def test_expand_keyword_arg(self, tmp_path: Path, results_root: Path) -> None:
        """results_root via --results-root flag on expand."""
        cli_runner = CliRunner()
        with patch(_RUNNER_MAIN) as mock_main:
            result = cli_runner.invoke(
                psimulate,
                [
                    "expand",
                    "--results-root",
                    str(results_root),
                    "--project",
                    "proj_simscience",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "deprecated" not in result.output.lower()
        mock_main.assert_called_once()


class TestPositionalArgDeprecationWarning:
    """Using the old positional arguments still works but emits a warning."""

    def test_run_positional_args_emit_warning(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        cli_runner = CliRunner()
        with pytest.warns(FutureWarning, match="deprecated"):
            with patch(_RUNNER_MAIN) as mock_main:
                result = cli_runner.invoke(
                    psimulate,
                    [
                        "run",
                        str(model_spec),
                        str(branch_config),
                        "--result-directory",
                        str(result_dir),
                        "--project",
                        "proj_simscience",
                    ],
                )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()
        call_kwargs = mock_main.call_args.kwargs
        assert call_kwargs["input_paths"].model_specification == model_spec.resolve()
        assert call_kwargs["input_paths"].branch_configuration == branch_config.resolve()

    def test_restart_positional_arg_emits_warning(
        self, tmp_path: Path, results_root: Path
    ) -> None:
        cli_runner = CliRunner()
        with pytest.warns(FutureWarning, match="deprecated"):
            with patch(_RUNNER_MAIN) as mock_main:
                result = cli_runner.invoke(
                    psimulate,
                    [
                        "restart",
                        str(results_root),
                        "--project",
                        "proj_simscience",
                    ],
                )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()

    def test_expand_positional_arg_emits_warning(
        self, tmp_path: Path, results_root: Path
    ) -> None:
        cli_runner = CliRunner()
        with pytest.warns(FutureWarning, match="deprecated"):
            with patch(_RUNNER_MAIN) as mock_main:
                result = cli_runner.invoke(
                    psimulate,
                    [
                        "expand",
                        str(results_root),
                        "--project",
                        "proj_simscience",
                    ],
                )

        assert result.exit_code == 0, result.output
        mock_main.assert_called_once()


class TestPositionalKeywordConflict:
    """Providing the same file path as both a positional arg and a keyword option
    raises an error."""

    def test_run_model_spec_conflict(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            psimulate,
            [
                "run",
                str(model_spec),  # positional
                "--model-specification",
                str(model_spec),  # keyword
                "--branch-configuration",
                str(branch_config),
                "--result-directory",
                str(result_dir),
                "--project",
                "proj_simscience",
            ],
        )
        assert result.exit_code != 0
        assert (
            "model_specification" in result.output.lower()
            or "conflict" in result.output.lower()
        )

    def test_restart_results_root_conflict(self, tmp_path: Path, results_root: Path) -> None:
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            psimulate,
            [
                "restart",
                str(results_root),  # positional
                "--results-root",
                str(results_root),  # keyword
                "--project",
                "proj_simscience",
            ],
        )
        assert result.exit_code != 0


class TestDeprecationDeadline:
    """This test fails after the deprecation deadline to remind us to remove
    positional argument support entirely."""

    def test_positional_args_deadline_not_reached(self) -> None:
        """If this test fails, it is time to remove positional argument support
        for model_specification, branch_configuration, and results_root.
        Replace them entirely with the keyword options --model-specification/-M,
        --branch-configuration/-B, and --results-root/-R."""
        today = datetime.date.today()
        assert today < _DEPRECATION_DEADLINE, (
            f"Positional argument deprecation deadline ({_DEPRECATION_DEADLINE}) has passed. "
            f"Remove positional argument support for model_specification, "
            f"branch_configuration, and results_root. Use the keyword options "
            f"--model-specification/-M, --branch-configuration/-B, and "
            f"--results-root/-R exclusively."
        )


class TestSimVerbosity:
    """Per-simulation verbosity must reach the assembled ``extra_args`` on the
    ``run`` subcommand, both via the ``-s`` count and the deprecated
    ``--sim-verbosity`` value option."""

    @staticmethod
    def _full_config(
        tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> Path:
        return _write_yaml(
            tmp_path,
            {
                "model_specification": str(model_spec),
                "branch_configuration": str(branch_config),
                "result_directory": str(result_dir),
                "project": "proj_simscience",
                "peak_memory": 5,
                "max_runtime": "01:00:00",
                "queue": "all.q",
                "max_workers": 100,
                "max_attempts": 2,
                "hardware": "r650",
            },
        )

    @pytest.mark.parametrize("args, expected", [([], 0), (["-s"], 1), (["-ss"], 2)])
    def test_count_reaches_extra_args(
        self,
        tmp_path: Path,
        model_spec: Path,
        branch_config: Path,
        result_dir: Path,
        args: list[str],
        expected: int,
    ) -> None:
        config = self._full_config(tmp_path, model_spec, branch_config, result_dir)
        with patch(_RUNNER_MAIN) as mock_main:
            result = CliRunner().invoke(
                psimulate, ["run", "--run-config", str(config), *args]
            )
        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["extra_args"]["sim_verbosity"] == expected

    def test_deprecated_value_reaches_extra_args(
        self, tmp_path: Path, model_spec: Path, branch_config: Path, result_dir: Path
    ) -> None:
        config = self._full_config(tmp_path, model_spec, branch_config, result_dir)
        with patch(_RUNNER_MAIN) as mock_main:
            result = CliRunner().invoke(
                psimulate,
                ["run", "--run-config", str(config), "--sim-verbosity", "2"],
            )
        assert result.exit_code == 0, result.output
        assert mock_main.call_args.kwargs["extra_args"]["sim_verbosity"] == 2
