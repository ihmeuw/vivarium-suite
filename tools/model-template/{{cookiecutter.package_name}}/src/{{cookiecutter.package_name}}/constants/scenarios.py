from typing import NamedTuple


#############
# Scenarios #
#############

class __Scenarios(NamedTuple):
    baseline: str = 'baseline'
    alternative: str = 'alternative'


SCENARIOS = __Scenarios()
