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

if TYPE_CHECKING:
    from vivarium.cluster_tools.core.jobmon.artifact import build_artifacts_in_parallel


def __getattr__(name: str) -> Any:
    """Import ``build_artifacts_in_parallel`` on first use; it needs Jobmon."""
    if name == "build_artifacts_in_parallel":
        from vivarium.cluster_tools.core.jobmon.artifact import build_artifacts_in_parallel

        return build_artifacts_in_parallel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
