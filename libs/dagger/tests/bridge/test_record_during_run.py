"""Tests of recording each bridged step when it finishes, while the Jobmon run is still going.

The builds run through :class:`~vivarium.dagger.harness.FakeJobmon` or a subclass of it,
so they need no Jobmon server. A test that looks at the build's root during the run does
so from the fake's ``run``, in the build's main thread, while the bridge's recording
thread works beside it.
"""

from pathlib import Path

import pytest

# Recording during the run


def test_an_input_edited_after_its_step_was_recorded_runs_the_step_in_the_next_build(
    tmp_path: Path,
) -> None:
    """When a root input of the first step is edited from the fake's run after that step's journal entry exists and before the run returns, the entry in pytask.lock holds the input's state from before the edit, and the next bridged build with stable code identities binds a plan that contains the first step."""


def test_every_submitted_step_has_exactly_one_report_and_one_lockfile_entry(
    tmp_path: Path,
) -> None:
    """A bridged build of the chain and the independent step, with a recording interval short enough that steps are recorded during the run, ends with exactly one SUCCESS report and one pytask.lock entry for each submitted step."""


def test_a_result_that_appears_after_the_last_pass_is_recorded_once_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a recording interval longer than the whole run, so the thread makes no pass after its first, every step still ends with exactly one SUCCESS report and one pytask.lock entry."""


# A step that fails on its node


def test_a_failure_result_is_not_recorded_or_reported_during_the_run(tmp_path: Path) -> None:
    """While the fake's run is still going after an entry whose result reports a failure, and after at least one more pass of the recording thread, the session has no report for that step and the journal has no entry for it."""


def test_a_step_that_succeeds_on_its_second_attempt_has_one_entry_and_one_success_report(
    tmp_path: Path,
) -> None:
    """Through a FakeJobmon subclass that runs a failed entry a second time, a step that fails on its first attempt and succeeds on its second ends with one pytask.lock entry and exactly one report, which is SUCCESS."""


def test_a_step_whose_attempts_all_fail_has_no_entry_and_one_fail_report(
    tmp_path: Path,
) -> None:
    """A step that fails on every attempt has no pytask.lock entry and no journal entry, and exactly one report, which is FAIL with a RemoteTaskFailed that holds the node's error."""


# A build that is stopped during the run


def test_sigterm_during_the_run_keeps_the_recorded_steps_and_settles_the_rest(
    tmp_path: Path,
) -> None:
    """A bridged build whose fake sends SIGTERM after the first entry was run and recorded returns with the first step as SUCCESS with a pytask.lock entry, every other submitted step with exactly one report that is FAIL or SKIP_PREVIOUS_FAILED, and a state file with status 'interrupted'."""


def test_a_jobmon_error_during_the_run_keeps_the_recorded_steps_and_settles_the_rest(
    tmp_path: Path,
) -> None:
    """A bridged build whose fake stops with an error after the first entry (error_after=1) returns with the first step as SUCCESS with a pytask.lock entry and every other submitted step with exactly one report that is FAIL or SKIP_PREVIOUS_FAILED."""


@pytest.mark.parametrize(
    "ending", ["finished", "sigterm", "keyboard_interrupt", "jobmon_error"]
)
def test_the_recording_thread_has_ended_when_the_build_returns(
    tmp_path: Path, ending: str
) -> None:
    """After a bridged build that finishes, is interrupted by SIGTERM or by a KeyboardInterrupt, or whose Jobmon run stops with an error, no thread that the build started is alive."""


def test_the_block_does_not_exit_before_the_thread_ends_when_an_interrupt_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _record_ready is replaced by a function that blocks until released, and the main thread gets a KeyboardInterrupt while _recording_during_the_run waits for the thread on exit, the block has not exited while the function is still blocked, and it exits after the function is released."""


def test_an_error_in_the_recording_thread_is_a_warning_and_every_step_is_still_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With _record_ready replaced by a function that raises, a bridged build still ends with one SUCCESS report and one pytask.lock entry for each step, and it prints a warning that names the error."""


# _record_ready


def test_record_ready_records_a_success_and_adds_it_to_recorded(tmp_path: Path) -> None:
    """_record_ready, given a submitted task whose result file reports success and whose product exists, appends one SUCCESS report for it, writes its journal entry, and adds its signature to the recorded set."""


def test_record_ready_records_in_plan_order_and_never_twice(tmp_path: Path) -> None:
    """_record_ready, called twice with two independent submitted tasks that both have success results, appends their reports in the order of the submitted list on the first call and appends nothing on the second."""


def test_record_ready_skips_a_result_that_cannot_be_read_and_records_it_later(
    tmp_path: Path,
) -> None:
    """_record_ready leaves a task whose result file holds half of a JSON document with no report and out of the recorded set, and a second call after the whole result is written records it as SUCCESS."""


def test_record_ready_leaves_a_failure_result(tmp_path: Path) -> None:
    """_record_ready leaves a task whose result file reports a failure with no report and out of the recorded set."""


def test_record_ready_leaves_a_success_whose_product_is_missing_and_records_it_once_present(
    tmp_path: Path,
) -> None:
    """_record_ready leaves a task whose result reports success while its product file does not exist with no report and out of the recorded set, and a second call after the product is written records it as SUCCESS."""


def test_a_success_whose_product_never_appears_is_one_fail_report_at_the_end_of_the_run(
    tmp_path: Path,
) -> None:
    """In a bridged build, a step whose node reports success without writing its product has no report while the run is going and ends with exactly one report, which is FAIL, and no pytask.lock entry."""


def test_record_ready_does_not_record_a_step_below_a_step_that_is_not_recorded(
    tmp_path: Path,
) -> None:
    """_record_ready, given a two-step chain in which both result files report success and the first step's product is missing, records neither step, and after the first step's product is written one further call records both in plan order."""


def test_a_step_below_a_step_that_failed_at_recording_is_skipped_and_has_no_entry(
    tmp_path: Path,
) -> None:
    """In a bridged build of a two-step chain in which the first step's node reports success without writing its product and the second step's node reports success, the first step ends as FAIL and the second as SKIP_PREVIOUS_FAILED, and neither has a pytask.lock entry."""
