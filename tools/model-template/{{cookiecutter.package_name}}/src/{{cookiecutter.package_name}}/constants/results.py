import itertools

import pandas as pd

from {{cookiecutter.package_name}}.constants import models

#################################
# Results columns and variables #
#################################

TOTAL_YLDS_COLUMN = 'years_lived_with_disability'
TOTAL_YLLS_COLUMN = 'years_of_life_lost'

# Columns from parallel runs
INPUT_DRAW_COLUMN = 'input_draw'
RANDOM_SEED_COLUMN = 'random_seed'

OUTPUT_INPUT_DRAW_COLUMN = 'input_data.input_draw_number'
OUTPUT_RANDOM_SEED_COLUMN = 'randomness.random_seed'
OUTPUT_SCENARIO_COLUMN = 'placeholder_branch_name.scenario'

STANDARD_COLUMNS = {
    'total_ylls': TOTAL_YLLS_COLUMN,
    'total_ylds': TOTAL_YLDS_COLUMN,
}

THROWAWAY_COLUMNS = [f'{state}_event_count' for state in models.STATES]

DEATH_COLUMN_TEMPLATE = "MEASURE_death_due_to_{CAUSE_OF_DEATH}_AGE_GROUP_{AGE_GROUP}_CURRENT_YEAR_{YEAR}_SEX_{SEX}"
YLLS_COLUMN_TEMPLATE = "MEASURE_ylls_due_to_{CAUSE_OF_DEATH}_AGE_GROUP_{AGE_GROUP}_CURRENT_YEAR_{YEAR}_SEX_{SEX}"
YLDS_COLUMN_TEMPLATE = (
    "MEASURE_ylds_due_to_{CAUSE_OF_DISABILITY}_AGE_GROUP_{AGE_GROUP}_CURRENT_YEAR_{YEAR}_SEX_{SEX}"
)
STATE_PERSON_TIME_COLUMN_TEMPLATE = (
    "MEASURE_{STATE}_person_time_AGE_GROUP_{AGE_GROUP}_CURRENT_YEAR_{YEAR}_SEX_{SEX}"
)
TRANSITION_COUNT_COLUMN_TEMPLATE = (
    "MEASURE_{TRANSITION}_event_count_AGE_GROUP_{AGE_GROUP}_CURRENT_YEAR_{YEAR}_SEX_{SEX}"
)

COLUMN_TEMPLATES = {
    'deaths': DEATH_COLUMN_TEMPLATE,
    'ylls': YLLS_COLUMN_TEMPLATE,
    'ylds': YLDS_COLUMN_TEMPLATE,
    'state_person_time': STATE_PERSON_TIME_COLUMN_TEMPLATE,
    'transition_count': TRANSITION_COUNT_COLUMN_TEMPLATE,
}

NON_COUNT_TEMPLATES = [
]

SEXES = ('male', 'female')
# TODO - add literals for years in the model
YEARS = ()
# TODO - add literals for ages in the model
AGE_GROUPS = ()
# TODO - add causes of death
CAUSES_OF_DEATH = (
    'other_causes',
    # models.FIRST_STATE_NAME,
)
# TODO - add causes of disability
CAUSES_OF_DISABILITY = (
    # models.FIRST_STATE_NAME,
    # models.SECOND_STATE_NAME,
)

TEMPLATE_FIELD_MAP = {
    'YEAR': YEARS,
    'SEX': SEXES,
    'AGE_GROUP': AGE_GROUPS,
    'CAUSE_OF_DEATH': CAUSES_OF_DEATH,
    'CAUSE_OF_DISABILITY': CAUSES_OF_DISABILITY,
    'STATE': models.STATES,
    'TRANSITION': models.TRANSITIONS,
}

{% raw %}
# noinspection PyPep8Naming
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


# noinspection PyPep8Naming
def RESULTS_MAP(kind):
    if kind not in COLUMN_TEMPLATES:
        raise ValueError(f'Unknown result column type {kind}')
    columns = []
    template = COLUMN_TEMPLATES[kind]
    filtered_field_map = {field: values
                          for field, values in TEMPLATE_FIELD_MAP.items() if f'{{{field}}}' in template}
    fields, value_groups = list(filtered_field_map.keys()), list(itertools.product(*filtered_field_map.values()))
    for value_group in value_groups:
        columns.append(template.format(**{field: value for field, value in zip(fields, value_group)}))
    df = pd.DataFrame(value_groups, columns=map(lambda x: x.lower(), fields))
    df['key'] = columns
    df['measure'] = kind  # per researcher feedback, this column is useful, even when it's identical for all rows
    return df.set_index('key').sort_index()
{% endraw %}
