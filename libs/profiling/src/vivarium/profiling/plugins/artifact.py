import re

from vivarium.framework.artifact import ArtifactManager as VivariumArtifactManager


class ArtifactManager(VivariumArtifactManager):
    """Customized ArtifactManager for Vivarium Profiling.

    The key difference is that when calling builder.data.load,
    we will strip the numeric suffix from the cause/risk name in the data key.
    """

    def load(self, key: str):
        """Strip numeric suffix from cause/risk names
        e.g.: 'risk_factor.high_systolic_blood_pressure_1.distribution'
        -> 'risk_factor.high_systolic_blood_pressure.distribution'
        """
        if len(key.split(".")) != 3:
            return super().load(key)

        entity_type, entity, measure = key.split(".")
        modified_entity = re.sub(r"(_\d+)$", "", entity)
        modified_key = f"{entity_type}.{modified_entity}.{measure}"
        return super().load(modified_key)
