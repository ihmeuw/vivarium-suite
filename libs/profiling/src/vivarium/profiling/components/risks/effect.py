import re

import pandas as pd
from vivarium.framework.engine import Builder
from vivarium_public_health.risks.effect import (
    NonLogLinearRiskEffect as NonLogLinearRiskEffect_,
)
from vivarium_public_health.risks.effect import RiskEffect as RiskEffect_

"""
Basic Risk Effect Wrapper for use in the MultiComponentParser
"""


class RiskEffect(RiskEffect_):
    def get_filtered_data(
        self, builder: Builder, data_source: str | float | pd.DataFrame
    ) -> float | pd.DataFrame:
        data = super().get_data(builder, data_source)

        if isinstance(data, pd.DataFrame):
            # filter data to only include the target entity and measure
            correct_target_mask = True
            columns_to_drop = []
            if "affected_entity" in data.columns:
                # THIS IS THE ONLY CHANGE! We need to filter to the non-suffixed name
                correct_target_mask &= data["affected_entity"] == re.sub(
                    r"(_\d+)$", "", self.target.name
                )
                columns_to_drop.append("affected_entity")
            if "affected_measure" in data.columns:
                correct_target_mask &= data["affected_measure"] == self.target.measure
                columns_to_drop.append("affected_measure")
            data = data[correct_target_mask].drop(columns=columns_to_drop)
        return data


class NonLogLinearRiskEffect(NonLogLinearRiskEffect_, RiskEffect):
    pass


from vivarium_public_health.risks.effect import (
    NonLogLinearRiskEffect as NonLogLinearRiskEffect_,
)
from vivarium_public_health.risks.effect import RiskEffect as RiskEffect_
