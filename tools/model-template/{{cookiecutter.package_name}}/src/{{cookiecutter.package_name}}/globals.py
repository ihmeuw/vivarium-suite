####################
# Project metadata #
####################

PROJECT_NAME = '{{cookiecutter.package_name}}'
CLUSTER_PROJECT = 'proj_cost_effect'

CLUSTER_QUEUE = 'all.q'
MAKE_ARTIFACT_MEM = '3G'
MAKE_ARTIFACT_CPU = '1'
MAKE_ARTIFACT_RUNTIME = '3:00:00'

LOCATIONS = [
    # TODO - project locations here
]


#############
# Data Keys #
#############

METADATA_LOCATIONS = 'metadata.locations'

POPULATION_STRUCTURE = 'population.structure'
POPULATION_AGE_BINS = 'population.age_bins'
POPULATION_DEMOGRAPHY = 'population.demographic_dimensions'

ALL_CAUSE_CSMR = 'cause.all_causes.cause_specific_mortality_rate'

# TODO - sample disease keys
DIARRHEA_CAUSE_SPECIFIC_MORTALITY_RATE = 'cause.diarrheal_diseases.cause_specific_mortality_rate'
DIARRHEA_PREVALENCE = 'cause.diarrheal_diseases.prevalence'
DIARRHEA_INCIDENCE_RATE = 'cause.diarrheal_diseases.incidence_rate'
DIARRHEA_REMISSION_RATE = 'cause.diarrheal_diseases.remission_rate'
DIARRHEA_EXCESS_MORTALITY_RATE = 'cause.diarrheal_diseases.excess_mortality_rate'
DIARRHEA_DISABILITY_WEIGHT = 'cause.diarrheal_diseases.disability_weight'
DIARRHEA_RESTRICTIONS = 'cause.diarrheal_diseases.restrictions'


### ------------- Reviewers: what, if any, of the following should be part of the template ------------- 
'''
###########################
# Disease Model variables #
###########################

# TODO - sample states and transitions
DIARRHEA_MODEL_NAME = 'diarrheal_diseases'
DIARRHEA_SUSCEPTIBLE_STATE_NAME = f'susceptible_to_{DIARRHEA_MODEL_NAME}'
DIARRHEA_WITH_CONDITION_STATE_NAME = DIARRHEA_MODEL_NAME
DIARRHEA_MODEL_STATES = (DIARRHEA_SUSCEPTIBLE_STATE_NAME, DIARRHEA_WITH_CONDITION_STATE_NAME)
DIARRHEA_MODEL_TRANSITIONS = (
    f'{DIARRHEA_SUSCEPTIBLE_STATE_NAME}_TO_{DIARRHEA_WITH_CONDITION_STATE_NAME}',
    f'{DIARRHEA_WITH_CONDITION_STATE_NAME}_TO_{DIARRHEA_SUSCEPTIBLE_STATE_NAME}',
)

# TODO - add all diseases
DISEASE_MODELS = (DIARRHEA_MODEL_NAME)
DISEASE_MODEL_MAP = {
    DIARRHEA_MODEL_NAME: {
        'states': DIARRHEA_MODEL_STATES,
        'transitions': DIARRHEA_MODEL_TRANSITIONS,
    },
}


#################################
# Results columns and variables #
#################################

TOTAL_POPULATION_COLUMN = 'total_population'
TOTAL_YLDS_COLUMN = 'years_lived_with_disability'
TOTAL_YLLS_COLUMN = 'years_of_life_lost'

STANDARD_COLUMNS = {
    'total_population': TOTAL_POPULATION_COLUMN,
    'total_ylls': TOTAL_YLLS_COLUMN,
    'total_ylds': TOTAL_YLDS_COLUMN,
}

TOTAL_POPULATION_COLUMN_TEMPLATE = 'total_population_{POP_STATE}'
PERSON_TIME_COLUMN_TEMPLATE = 'person_time_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'
DEATH_COLUMN_TEMPLATE = 'death_due_to_{CAUSE_OF_DEATH}_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'
YLLS_COLUMN_TEMPLATE = 'ylls_due_to_{CAUSE_OF_DEATH}_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'
YLDS_COLUMN_TEMPLATE = 'ylds_due_to_{CAUSE_OF_DISABILITY}_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'
STATE_PERSON_TIME_COLUMN_TEMPLATE = '{STATE}_person_time_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'
TRANSITION_COUNT_COLUMN_TEMPLATE = '{TRANSITION}_event_count_in_{YEAR}_among_{SEX}_in_age_group_{AGE_GROUP}'


COLUMN_TEMPLATES = {
    'population': TOTAL_POPULATION_COLUMN_TEMPLATE,
    'person_time': PERSON_TIME_COLUMN_TEMPLATE,
    'deaths': DEATH_COLUMN_TEMPLATE,
    'ylls': YLLS_COLUMN_TEMPLATE,
    'ylds': YLDS_COLUMN_TEMPLATE,
    'state_person_time': STATE_PERSON_TIME_COLUMN_TEMPLATE,
    'transition_count': TRANSITION_COUNT_COLUMN_TEMPLATE,
}


POP_STATES = ('living', 'dead', 'tracked', 'untracked')
YEARS = ('2020', '2021', '2022')
SEXES = ('male', 'female')
AGE_GROUPS = ('early_neonatal', 'late_neonatal', 'post_neonatal', '1_to_4')
CAUSES_OF_DEATH = (
    'other_causes',
    DIARRHEA_WITH_CONDITION_STATE_NAME,
)
CAUSES_OF_DISABILITY = (
    DIARRHEA_WITH_CONDITION_STATE_NAME,
)
STATES = (state for model in DISEASE_MODELS for state in DISEASE_MODEL_MAP[model]['states'])
TRANSITIONS = (transition for model in DISEASE_MODELS for transition in DISEASE_MODEL_MAP[model]['transitions'])
BIRTH_STATES = (NEONATAL_DISORDERS_WITH_CONDITION_STATE_NAME,)


TEMPLATE_FIELD_MAP = {
    'POP_STATE': POP_STATES,
    'YEAR': YEARS,
    'SEX': SEXES,
    'AGE_GROUP': AGE_GROUPS,
    'CAUSE_OF_DEATH': CAUSES_OF_DEATH,
    'CAUSE_OF_DISABILITY': CAUSES_OF_DISABILITY,
    'STATE': STATES,
    'TRANSITION': TRANSITIONS,
}


def RESULT_COLUMNS(kind='all'):
    if kind not in COLUMN_TEMPLATES and kind != 'all':
        raise ValueError(f'Unknown result column type {kind}')
    columns = []
    if kind == 'all':
        for k in COLUMN_TEMPLATES:
            columns += RESULT_COLUMNS(k)
        columns = list(STANDARD_COLUMNS.values()) + columns
    else:
        template = COLUMN_TEMPLATES[kind]
        filtered_field_map = {field: values
                              for field, values in TEMPLATE_FIELD_MAP.items() if f'{{{field}}}' in template}
        fields, value_groups = filtered_field_map.keys(), itertools.product(*filtered_field_map.values())
        for value_group in value_groups:
            columns.append(template.format(**{field: value for field, value in zip(fields, value_group)}))
    return columns
'''