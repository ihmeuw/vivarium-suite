"""
======
jobmon
======

Jobmon integration layer: SDK facade (:mod:`.client`), env resolution
(:mod:`.env`), shared Click decorators (:mod:`.cli_options`), and the
parallel artifact-build workflow runner (:mod:`.artifact`).

"""

from vivarium.cluster_tools.core.jobmon.artifact import build_artifacts_in_parallel
from vivarium.cluster_tools.core.jobmon.cli_options import with_max_attempts, with_max_workers
