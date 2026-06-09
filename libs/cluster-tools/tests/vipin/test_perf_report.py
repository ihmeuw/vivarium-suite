import pandas as pd
from loguru import logger

from vivarium.cluster_tools.vipin.perf_report import print_stat_report


def test_print_stat_report_emits_at_warning() -> None:
    """The end-of-run performance report is emitted at WARNING so it stays
    visible under psimulate's default (WARNING) terminal verbosity."""
    levels: list[str] = []
    sink_id = logger.add(
        lambda message: levels.append(message.record["level"].name), level="DEBUG"
    )
    try:
        # Minimal performance frame: one exec_time_ column, no scenario columns.
        perf_df = pd.DataFrame({"exec_time_total_minutes": [1.0, 2.0, 3.0]})
        print_stat_report(perf_df, scenario_cols=[])
    finally:
        logger.remove(sink_id)

    # The report must be logged, and at WARNING (not INFO, which would be hidden
    # under the default verbosity).
    assert levels == ["WARNING"]
