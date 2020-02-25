

# Cluster projects under the purview of Simulation Science
CLUSTER_PROJECTS = ['proj_cost_effect', 'proj_cost_effect_diarrhea', 'proj_cost_effect_dcpn', 'proj_cost_effect_conic',
                    'proj_csu']
DEFAULT_CLUSTER_PROJECT = 'proj_cost_effect'

DEFAULT_OUTPUT_DIRECTORY = '/share/costeffectiveness/results'

DEFAULT_JOBS_PER_REDIS_INSTANCE = 1000
DEFAULT_THREADS_PER_JOB = 1

# Cluster specific parameters
ALL_Q_MAX_RUNTIME_HOURS = 3 * 24
LONG_Q_MAX_RUNTIME_HOURS = 16 * 24

CLUSTER_ENV_HOSTNAME = 'HOSTNAME'
SUBMIT_HOST_MARKER = '-submit-'
