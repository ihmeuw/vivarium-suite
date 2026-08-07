"""Tests for the task_runner module (Jobmon task CLI entry point).

These tests verify the orchestration logic in task_runner.main():
argument parsing, metadata loading, command dispatch routing, result
plumbing, and logging setup.  The actual work horses and result writing
are mocked — they have their own dedicated test suites.
"""

import io
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pandas as pd
import pytest
from loguru import logger

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate import COMMANDS
from vivarium.cluster_tools.psimulate.jobs import JobParameters
from vivarium.cluster_tools.psimulate.results.writing import write_metadata
from vivarium.cluster_tools.psimulate.worker import PERF_LOG_MARKER
from vivarium.cluster_tools.psimulate.worker.task_runner import (
    _configure_worker_logging,
    main,
    parse_args,
)
from vivarium.cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext,
)

# Patch targets are the names as imported into task_runner.
_WORK_HORSE = "vivarium.cluster_tools.psimulate.worker.task_runner.work_horse"
_LOAD_TEST_WORK_HORSE = (
    "vivarium.cluster_tools.psimulate.worker.task_runner.load_test_work_horse"
)
_WRITE_TASK_RESULTS = "vivarium.cluster_tools.psimulate.worker.task_runner.write_task_results"

# Python's own traceback header, so counting reported tracebacks survives any
# reformatting of the sinks' log layout.
_TRACEBACK_HEADER = "Traceback (most recent call last)"
_FAILURE_MESSAGE = "task-failure-marker"
_DEBUG_PROBE = "debug-probe"
_INFO_PROBE = "info-probe"
_PERF_PAYLOAD = "perf-payload-probe"


def _failing_work_horse(job_parameters: JobParameters) -> dict[str, pd.DataFrame]:
    """Stand in for a simulation that dies partway through."""
    raise RuntimeError(_FAILURE_MESSAGE)


def _emit_level_probes() -> None:
    """Emit one DEBUG and one INFO record to probe the stdout sink's floor."""
    logger.debug(_DEBUG_PROBE)
    logger.info(_INFO_PROBE)


@pytest.fixture(scope="module")
def job_params() -> JobParameters:
    return make_job_parameters(input_draw=1, random_seed=42)


def _build_argv(
    metadata_dir: Path,
    results_dir: Path,
    command: str,
    task_id: str,
) -> list[str]:
    """Build a CLI argv list for ``main()``."""
    return [
        "--metadata-dir",
        str(metadata_dir),
        "--task-id",
        task_id,
        "--results-dir",
        str(results_dir),
        "--command",
        command,
    ]


@pytest.fixture()
def dirs(tmp_path: Path) -> dict[str, Path]:
    """Create and return the three directories used by task_runner."""
    d = {
        "metadata": tmp_path / "metadata",
        "results": tmp_path / "results",
        "worker_logs": tmp_path / "worker_logs",
    }
    for p in d.values():
        p.mkdir()
    return d


def _assert_only_on(
    out: str, err: str, marker: str, stream: Literal["stdout", "stderr"]
) -> None:
    """Assert ``marker`` reached the named stream and not the other one."""
    expected, forbidden = (out, err) if stream == "stdout" else (err, out)
    assert marker in expected
    assert marker not in forbidden


def _run_main_with_patched_work_horses(
    dirs: dict[str, Path],
    job_parameters: JobParameters,
    command: str = COMMANDS.run,
    during_task: Callable[[], None] | None = None,
    work_horse_side_effect: (
        Callable[[JobParameters], dict[str, pd.DataFrame]] | BaseException | None
    ) = None,
    write_task_results_side_effect: BaseException | None = None,
) -> int:
    """Write the task metadata and drive ``main()`` with the work horses and
    result writing patched out.

    ``during_task`` runs inside whichever work horse the command dispatches to,
    so records it emits are subject to the logging ``main()`` configured.
    """

    def run_task(job_parameters: JobParameters) -> dict[str, pd.DataFrame]:
        if during_task is not None:
            during_task()
        return {"some_metric": pd.DataFrame({"a": [1]})}

    def run_load_test(job_parameters: JobParameters) -> pd.DataFrame:
        if during_task is not None:
            during_task()
        return pd.DataFrame({"x": [1]})

    write_metadata(dirs["metadata"], job_parameters)
    with (
        patch(_WORK_HORSE, side_effect=work_horse_side_effect or run_task),
        patch(_LOAD_TEST_WORK_HORSE, side_effect=run_load_test),
        patch(_WRITE_TASK_RESULTS, side_effect=write_task_results_side_effect),
    ):
        return main(
            _build_argv(
                dirs["metadata"],
                dirs["results"],
                command=command,
                task_id=job_parameters.task_id,
            )
        )


class TestParseArgs:
    def test_valid_args(self, tmp_path: Path) -> None:
        argv = [
            "--metadata-dir",
            str(tmp_path / "meta"),
            "--task-id",
            "abc123",
            "--results-dir",
            str(tmp_path / "res"),
            "--command",
            "run",
        ]
        ns = parse_args(argv)
        assert ns.metadata_dir == tmp_path / "meta"
        assert ns.task_id == "abc123"
        assert ns.results_dir == tmp_path / "res"
        assert ns.command == "run"
        assert isinstance(ns.metadata_dir, Path)
        assert isinstance(ns.task_id, str)

    def test_missing_required_arg_raises_system_exit(self, tmp_path: Path) -> None:
        """Omitting any required argument must trigger SystemExit (argparse)."""
        with pytest.raises(SystemExit):
            parse_args(["--metadata-dir", str(tmp_path)])

    def test_unknown_arg_raises_system_exit(self, tmp_path: Path) -> None:
        argv = _build_argv(tmp_path, tmp_path, command="run", task_id="x") + ["--bogus"]
        with pytest.raises(SystemExit):
            parse_args(argv)


class TestMainDispatch:
    """main() routes to the correct work horse based on the command field."""

    @pytest.mark.parametrize("command", [COMMANDS.run, COMMANDS.restart, COMMANDS.expand])
    def test_vivarium_commands_call_work_horse(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        command: str,
        _restore_loguru: None,
    ) -> None:
        write_metadata(dirs["metadata"], job_params)
        mock_results = {"some_metric": pd.DataFrame({"a": [1]})}

        with (
            patch(_WORK_HORSE, return_value=mock_results) as work_horse,
            patch(_LOAD_TEST_WORK_HORSE) as load_test_work_horse,
            patch(_WRITE_TASK_RESULTS) as write,
        ):
            main(
                _build_argv(
                    dirs["metadata"],
                    dirs["results"],
                    command=command,
                    task_id=job_params.task_id,
                )
            )

            work_horse.assert_called_once()
            load_test_work_horse.assert_not_called()

            # Verify the JobParameters passed to work_horse
            args, kwargs = work_horse.call_args
            assert isinstance(args[0], JobParameters)
            assert args[0].input_draw == job_params.input_draw
            assert args[0].random_seed == job_params.random_seed

            # Verify write_task_results receives the work_horse return value
            write.assert_called_once_with(
                results_dir=dirs["results"],
                job_parameters=args[0],
                results_dict=mock_results,
            )

    def test_load_test_calls_load_test_work_horse(
        self, dirs: dict[str, Path], job_params: JobParameters, _restore_loguru: None
    ) -> None:
        write_metadata(dirs["metadata"], job_params)
        mock_df = pd.DataFrame({"x": [1, 2, 3]})

        with (
            patch(_WORK_HORSE) as work_horse,
            patch(_LOAD_TEST_WORK_HORSE, return_value=mock_df) as load_test_work_horse,
            patch(_WRITE_TASK_RESULTS) as write,
        ):
            main(
                _build_argv(
                    dirs["metadata"],
                    dirs["results"],
                    command=COMMANDS.load_test,
                    task_id=job_params.task_id,
                )
            )

            load_test_work_horse.assert_called_once()
            work_horse.assert_not_called()

            args, kwargs = load_test_work_horse.call_args
            assert isinstance(args[0], JobParameters)

    def test_unknown_command_is_reported_as_a_failure(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
    ) -> None:
        """An unrecognized command fails the task via a non-zero return code and
        a logged traceback naming the command, rather than propagating."""
        return_code = _run_main_with_patched_work_horses(
            dirs, job_params, command="bogus_command"
        )

        assert return_code != 0
        out, err = capfd.readouterr()
        assert _TRACEBACK_HEADER in err
        assert "bogus_command" in err
        # main() echoes the command at INFO on stdout, so only the traceback's
        # absence distinguishes the two streams here.
        assert _TRACEBACK_HEADER not in out


class TestMainFailureReporting:
    """A failing task reports exactly once, at the process boundary."""

    def test_work_horse_failure_returns_nonzero(
        self, dirs: dict[str, Path], job_params: JobParameters, _restore_loguru: None
    ) -> None:
        """A work horse raising makes ``main()`` return a non-zero exit code
        instead of propagating, so Jobmon records the task as failed."""
        return_code = _run_main_with_patched_work_horses(
            dirs, job_params, work_horse_side_effect=_failing_work_horse
        )

        assert return_code != 0

    def test_traceback_is_written_to_stderr_exactly_once(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
    ) -> None:
        """The failing work horse's traceback appears once, on stderr only — not
        once from the work horse and again from an unhandled re-raise, and not on
        stdout, which the stream split reserves for everything below ERROR."""
        _run_main_with_patched_work_horses(
            dirs, job_params, work_horse_side_effect=_failing_work_horse
        )

        out, err = capfd.readouterr()
        # Jobmon POSTs only the last 10,000 characters of stderr as the error
        # description, so a second copy costs half that budget.
        assert err.count(_TRACEBACK_HEADER) == 1
        assert err.count(f"RuntimeError: {_FAILURE_MESSAGE}") == 1
        _assert_only_on(out, err, _TRACEBACK_HEADER, "stderr")
        _assert_only_on(out, err, _FAILURE_MESSAGE, "stderr")

    def test_result_write_failure_is_reported(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
    ) -> None:
        """A failure after the work horse returns — here in ``write_task_results``
        — is logged too, a path the work horses' own handlers never covered."""
        return_code = _run_main_with_patched_work_horses(
            dirs,
            job_params,
            write_task_results_side_effect=OSError(_FAILURE_MESSAGE),
        )

        assert return_code != 0
        out, err = capfd.readouterr()
        assert err.count(_TRACEBACK_HEADER) == 1
        _assert_only_on(out, err, f"OSError: {_FAILURE_MESSAGE}", "stderr")


class TestMainMissingMetadata:
    def test_missing_metadata_file_raises(self, dirs: dict[str, Path]) -> None:
        """If the metadata JSON does not exist, main() should raise."""
        with pytest.raises(FileNotFoundError):
            main(
                _build_argv(
                    dirs["metadata"],
                    dirs["results"],
                    command=COMMANDS.run,
                    task_id="nonexistent",
                )
            )


@pytest.fixture
def _restore_loguru() -> Generator[None, None, None]:
    # _configure_worker_logging removes all loguru sinks with no teardown; restore
    # a default sink so the routing doesn't leak to later tests on the same worker.
    yield
    logger.remove()
    logger.add(sys.stderr)


@pytest.fixture
def _fresh_process_handler_ids() -> Generator[None, None, None]:
    # vivarium.engine's LoggingManager decides whether a terminal sink already
    # exists by checking for loguru handler id 1, which lands on the worker's
    # stdout sink only because a freshly started process has issued exactly one
    # id already (loguru's own default handler, id 0). That counter is global and
    # never rewinds, so without this reset the ids earlier tests consumed would
    # make the engine interaction order-dependent.
    core = logger._core  # type: ignore[attr-defined]
    original_count = core.handlers_count
    core.handlers_count = 1
    yield
    core.handlers_count = max(original_count, core.handlers_count)


class TestConfigureWorkerLogging:
    """Tests for ``_configure_worker_logging`` — the stdout/stderr split and
    level resolution behind the SLURM ``.o``/``.e`` worker logs."""

    @pytest.mark.parametrize("level", ["info", "success", "warning"])
    def test_records_below_error_go_to_stdout_only(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None, level: str
    ) -> None:
        """Records below ERROR reach stdout and never stderr."""
        # The sinks must be installed while capfd is capturing: a loguru sink
        # holds the stream object it was handed.
        _configure_worker_logging(sim_verbosity=0)
        marker = f"{level}-marker"

        getattr(logger, level)(marker)

        out, err = capfd.readouterr()
        _assert_only_on(out, err, marker, "stdout")

    @pytest.mark.parametrize("level", ["error", "critical"])
    def test_error_and_above_go_to_stderr_only(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None, level: str
    ) -> None:
        """ERROR and CRITICAL reach stderr and never stdout — the overlap this
        ticket exists to remove."""
        _configure_worker_logging(sim_verbosity=0)
        marker = f"{level}-marker"

        getattr(logger, level)(marker)

        out, err = capfd.readouterr()
        _assert_only_on(out, err, marker, "stderr")

    def test_exception_traceback_is_not_duplicated(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None
    ) -> None:
        """``logger.exception`` writes its traceback to stderr only."""
        _configure_worker_logging(sim_verbosity=0)

        try:
            raise RuntimeError("boom-marker")
        except RuntimeError:
            logger.exception("exception-marker")

        out, err = capfd.readouterr()
        _assert_only_on(out, err, "exception-marker", "stderr")
        _assert_only_on(out, err, "boom-marker", "stderr")
        assert "Traceback" in err

    def test_debug_excluded_from_stdout_when_sim_verbosity_is_zero(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None
    ) -> None:
        """A sim_verbosity of 0 floors stdout at INFO, dropping DEBUG."""
        _configure_worker_logging(sim_verbosity=0)

        _emit_level_probes()

        out, err = capfd.readouterr()
        assert _DEBUG_PROBE not in out
        assert _DEBUG_PROBE not in err
        # The INFO record proves the stdout sink is live, so the absence of the
        # DEBUG record is the level floor and not a capture failure.
        assert _INFO_PROBE in out

    @pytest.mark.parametrize("sim_verbosity", [1, 2])
    def test_debug_included_on_stdout_when_sim_verbosity_is_at_least_one(
        self,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
        sim_verbosity: int,
    ) -> None:
        """A sim_verbosity of 1 or more lowers the stdout floor to DEBUG; 2
        behaves identically to 1."""
        _configure_worker_logging(sim_verbosity=sim_verbosity)

        _emit_level_probes()

        out, err = capfd.readouterr()
        _assert_only_on(out, err, _DEBUG_PROBE, "stdout")

    def test_perf_log_records_are_excluded_from_stdout(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None
    ) -> None:
        """A record bound with ``PERF_LOG_MARKER`` reaches its own sink but not
        stdout, while an unmarked DEBUG record still reaches stdout."""
        # A DEBUG floor is the only configuration in which a perf record could
        # have reached stdout at all.
        _configure_worker_logging(sim_verbosity=1)
        # Stand in for the epilogue's dedicated perf sink, which is attached
        # after the worker's own sinks and so survives their installation.
        perf_sink = io.StringIO()
        logger.add(
            perf_sink,
            level="DEBUG",
            filter=lambda record: bool(record["extra"].get(PERF_LOG_MARKER, False)),
        )

        logger.bind(**{PERF_LOG_MARKER: True}).debug(_PERF_PAYLOAD)
        logger.debug(_DEBUG_PROBE)

        out, _err = capfd.readouterr()
        assert _PERF_PAYLOAD not in out
        assert _PERF_PAYLOAD in perf_sink.getvalue()
        # The exclusion is the marker's doing, not a blanket DEBUG drop.
        assert _DEBUG_PROBE in out

    def test_preexisting_sinks_are_removed(
        self, capfd: pytest.CaptureFixture[str], _restore_loguru: None
    ) -> None:
        """Loguru's default handler is cleared, so no record is duplicated onto
        the interpreter's own stderr."""
        preexisting = io.StringIO()
        logger.add(preexisting, level="DEBUG")

        _configure_worker_logging(sim_verbosity=0)
        logger.info("info-marker")

        out, _err = capfd.readouterr()
        assert preexisting.getvalue() == ""
        assert "info-marker" in out


class TestEngineTerminalLoggingInteraction:
    """The split has to survive ``vivarium.engine`` configuring its own terminal
    logging when a simulation is built inside the worker."""

    def test_simulation_context_adds_no_unfiltered_stdout_sink(
        self,
        capfd: pytest.CaptureFixture[str],
        _fresh_process_handler_ids: None,
        _restore_loguru: None,
    ) -> None:
        """Constructing a simulation context must not put ERROR records back on
        stdout: the engine adds a terminal sink only when it sees none."""
        _configure_worker_logging(sim_verbosity=0)

        ParallelSimulationContext()

        logger.info(_INFO_PROBE)
        logger.error("error-marker")

        out, err = capfd.readouterr()
        # An additional, unfiltered stdout sink would both duplicate the INFO
        # record and carry the ERROR record onto stdout.
        assert out.count(_INFO_PROBE) == 1
        _assert_only_on(out, err, "error-marker", "stderr")


class TestMainConfiguresLoggingFromMetadata:
    """``main()`` resolves the worker's log levels from the task metadata."""

    def test_metadata_without_sim_verbosity_does_not_raise(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
    ) -> None:
        """Metadata with no ``sim_verbosity`` key — the ``load_test`` shape —
        configures logging at the default level instead of raising."""
        assert "sim_verbosity" not in job_params.extras

        return_code = _run_main_with_patched_work_horses(
            dirs,
            job_params,
            command=COMMANDS.load_test,
            during_task=_emit_level_probes,
        )

        assert return_code == 0
        out, err = capfd.readouterr()
        assert _INFO_PROBE in out
        assert _DEBUG_PROBE not in out
        assert _DEBUG_PROBE not in err

    @pytest.mark.parametrize("sim_verbosity, debug_expected", [(0, False), (1, True)])
    def test_metadata_sim_verbosity_sets_the_stdout_floor(
        self,
        dirs: dict[str, Path],
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
        sim_verbosity: int,
        debug_expected: bool,
    ) -> None:
        """``extras["sim_verbosity"]`` in the metadata JSON reaches the stdout
        sink's level."""
        job_parameters = make_job_parameters(extras={"sim_verbosity": sim_verbosity})

        _run_main_with_patched_work_horses(
            dirs, job_parameters, during_task=_emit_level_probes
        )

        out, err = capfd.readouterr()
        assert (_DEBUG_PROBE in out) is debug_expected
        assert _DEBUG_PROBE not in err
        assert _INFO_PROBE in out

    def test_routing_holds_end_to_end_through_main(
        self,
        dirs: dict[str, Path],
        job_params: JobParameters,
        capfd: pytest.CaptureFixture[str],
        _restore_loguru: None,
    ) -> None:
        """Driving ``main()`` with a real metadata file keeps errors off stdout
        and non-errors off stderr across the whole worker path."""

        def emit_both_bands() -> None:
            logger.warning("warning-marker")
            logger.error("error-marker")

        _run_main_with_patched_work_horses(dirs, job_params, during_task=emit_both_bands)

        out, err = capfd.readouterr()
        _assert_only_on(out, err, "warning-marker", "stdout")
        _assert_only_on(out, err, "error-marker", "stderr")
