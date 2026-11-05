"""
==================
psimulate Workers
==================

Worker modules for psimulate Jobmon tasks.

The ``task_runner`` module is the CLI entry point that Jobmon invokes for each
task. It loads task metadata, dispatches to the appropriate work horse, and
writes results to disk.

Work horses contain the actual execution logic:

* ``vivarium_work_horse`` – runs a Vivarium simulation.
* ``load_test_work_horse`` – runs synthetic load tests.
"""

# Key bound into a log record's ``extra`` to mark it as belonging only in the
# dedicated performance log file, so the worker's stdout sink can drop it.
PERF_LOG_MARKER = "perf_log"
