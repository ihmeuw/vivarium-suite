"""
====================
FailOnce Component
====================

A vivarium component that fails on its first execution for a given
(input_draw, random_seed) pair and succeeds on subsequent attempts.

This enables testing of Jobmon's workflow resume: the initial ``psimulate run``
produces partial failures, and ``psimulate restart`` (with ``resume=True``)
retries only the failed tasks and they now succeed.

**Mechanism**: A file-based sentinel on the shared filesystem.  The sentinel
directory is communicated via the ``FAIL_ONCE_SENTINEL_DIR`` environment
variable — *not* through the simulation configuration — so it has no effect on
Jobmon workflow_args or task hashes.

If ``FAIL_ONCE_SENTINEL_DIR`` is unset the component is a no-op, so it is safe
to include in a model specification that is also used without the env var.

"""

import os
from pathlib import Path

from vivarium import Component


class FailOnceComponent(Component):
    """Fail the first time a (draw, seed) pair is executed; succeed thereafter.

    On ``setup``:

    1. Read ``FAIL_ONCE_SENTINEL_DIR`` from the environment.
    2. If unset → do nothing (component is inert).
    3. Construct a sentinel path:
       ``{sentinel_dir}/{input_draw}_{random_seed}``.
    4. If the sentinel file **exists** → the task already failed once and
       this is the retry; succeed silently.
    5. If the sentinel file **does not exist** → create it and raise
       ``RuntimeError`` to kill the task.
    """

    @property
    def columns_required(self) -> list[str]:
        return []

    def setup(self, builder: "vivarium.framework.engine.Builder") -> None:  # type: ignore[name-defined]  # noqa: F821
        sentinel_dir = os.environ.get("FAIL_ONCE_SENTINEL_DIR")
        if not sentinel_dir:
            return

        draw = builder.configuration.input_data.input_draw_number
        seed = builder.configuration.randomness.random_seed
        sentinel_path = Path(sentinel_dir) / f"{draw}_{seed}"

        if sentinel_path.exists():
            # Retry attempt — succeed.
            return

        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.touch()
        raise RuntimeError(
            f"FailOnceComponent: intentional first-attempt failure "
            f"(draw={draw}, seed={seed}, sentinel={sentinel_path})"
        )
