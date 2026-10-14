"""Run make targets in order, continue past failures, and summarize the results.

Backs ``make check`` and ``make lint``, so a single run reports every failing
target instead of stopping at the first one. Usage::

    python -m vivarium.build_utils.run_steps <target> [<target> ...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence


def run_make_target(target: str) -> int:
    """Run ``make <target>`` in the current directory and return its exit code."""
    return subprocess.run(["make", target]).returncode


def format_summary(results: dict[str, bool]) -> str:
    """Format the pass/fail result of each target as a summary block.

    Parameters
    ----------
    results
        Whether each target passed, in the order the targets ran.

    Returns
    -------
        The summary text, ending with the count of failed targets.
    """
    lines = ["", "========== Summary =========="]
    lines += [
        f"{'PASSED' if passed else 'FAILED'}  {target}" for target, passed in results.items()
    ]
    n_failed = list(results.values()).count(False)
    if n_failed:
        lines.append(f"{n_failed} of {len(results)} targets failed")
    else:
        lines.append(f"All {len(results)} targets passed")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    run_target: Callable[[str], int] = run_make_target,
) -> int:
    """Run each target, print a summary, and return the combined exit code.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).
    run_target
        Run one target and return its exit code. Tests replace this to avoid
        calling ``make``.

    Returns
    -------
        Process exit code: 0 if every target passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-run-steps")
    parser.add_argument("targets", nargs="+", help="Make targets to run, in order.")
    args = parser.parse_args(argv)

    results: dict[str, bool] = {}
    for target in args.targets:
        # Flush so the header lands before the subprocess output when stdout is a pipe.
        print(f"\n>>> make {target}", flush=True)
        results[target] = run_target(target) == 0

    print(format_summary(results), flush=True)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
