"""
======
jobmon
======

Jobmon integration layer: SDK facade (:mod:`.client`), env resolution
(:mod:`.env`), shared Click decorators (:mod:`.cli_options`), and the
parallel artifact-build workflow runner (:mod:`.artifact`).

"""

from typing import TYPE_CHECKING, Any

from vivarium.cluster_tools.core.jobmon.cli_options import with_max_attempts, with_max_workers

# TODO(MIC-7727): drop this package-level re-export in 5.0.0; nothing imports it from
# here. This module then needs no lazy import to stay free of Jobmon.
if TYPE_CHECKING:
    from vivarium.cluster_tools.core.jobmon.artifact import build_artifacts_in_parallel
else:
    # Hidden from type checkers so a misspelled import from this package is still reported.

    def __getattr__(name: str) -> Any:
        """Import ``build_artifacts_in_parallel`` on first use; it needs Jobmon."""
        if name == "build_artifacts_in_parallel":
            from vivarium.cluster_tools.core.jobmon.artifact import (
                build_artifacts_in_parallel,
            )

            return build_artifacts_in_parallel
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
