"""Engine's artifact integration: lifecycle plumbing on top of vivarium-artifact.

This subpackage holds engine's bindings between the simulation lifecycle
(Builder, plugin system, configuration parsing) and the data-artifact model
that lives in the separate ``vivarium-artifact`` distribution. Only
engine-owned names are exposed: ``ArtifactInterface``, ``ArtifactManager``,
and the helpers ``filter_data``, ``parse_artifact_path_config``,
``validate_filter_term``.

For the data model itself - ``Artifact``, ``ArtifactException``,
``EntityKey`` - import from ``vivarium.artifact`` directly.
"""

from vivarium.engine.framework.artifact.interface import ArtifactInterface
from vivarium.engine.framework.artifact.manager import (
    ArtifactManager,
    filter_data,
    parse_artifact_path_config,
    validate_filter_term,
)
