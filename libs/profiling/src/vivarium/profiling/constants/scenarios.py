from typing import NamedTuple

#############
# Scenarios #
#############


class InterventionScenario:
    def __init__(
        self,
        name: str,
        # todo add additional interventions
        # has_treatment_one: bool = False,
        # has_treatment_two: bool = False,
    ):
        self.name = name
        # self.has_treatment_one = has_treatment_one
        # self.has_treatment_two = has_treatment_two


class __InterventionScenarios(NamedTuple):
    BASELINE: InterventionScenario = InterventionScenario("baseline")
    # todo add additional intervention scenarios

    def __get_item__(self, item):
        return self._asdict()[item]


INTERVENTION_SCENARIOS = __InterventionScenarios()
