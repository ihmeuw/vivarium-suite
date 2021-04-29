from typing import NamedTuple

####################
# Project metadata #
####################

PROJECT_NAME = '{{cookiecutter.package_name}}'
CLUSTER_PROJECT = 'proj_cost_effect'
# # TODO use proj_csu if a csu project
# CLUSTER_PROJECT = 'proj_csu'

CLUSTER_QUEUE = 'all.q'
MAKE_ARTIFACT_MEM = '10G'
MAKE_ARTIFACT_CPU = '1'
MAKE_ARTIFACT_RUNTIME = '3:00:00'
MAKE_ARTIFACT_SLEEP = 10

LOCATIONS = [
    # TODO - project locations here
]


class __Scenarios(NamedTuple):
    baseline: str = 'baseline'
    # TODO - add scenarios here


SCENARIOS = __Scenarios()
