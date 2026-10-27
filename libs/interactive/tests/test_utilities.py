from tests.conftest import FakeWidget
from vivarium.interactive.utilities import log_progress


def test_log_progress_finishes_when_stopped_early(
    progress_widgets: list[FakeWidget],
) -> None:
    """A caller that stops iterating early still leaves the bar marked as finished."""
    steps = log_progress(range(5), name="Step")
    next(steps)
    next(steps)
    steps.close()

    progress = progress_widgets[0]
    assert progress.bar_style == "success"
    assert progress.value == 2
