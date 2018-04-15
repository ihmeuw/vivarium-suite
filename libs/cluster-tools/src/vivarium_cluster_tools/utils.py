from datetime import datetime
import os
import socket
import subprocess

import numpy as np

from vivarium.framework.util import collapse_nested_dict


def get_results_root(results_directory, simulation_configuration):
    launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    config_name = os.path.basename(simulation_configuration.rpartition('.')[0])
    if results_directory[-1] != '/':
        results_directory += '/'
    return results_directory + f"{config_name}/{launch_time}"


def get_log_path(results_root, log_path):
    if '{results_root}' in log_path:
        return log_path.format(results_root=results_root)
    else:
        return log_path  # Could be more defensive


def job_already_run(existing_outputs, input_draw, random_seed, branch_config):
    if existing_outputs is not None:
        mask = existing_outputs.input_draw == int(input_draw)
        mask &= existing_outputs.random_seed == int(random_seed)
        for k, v in collapse_nested_dict(branch_config):
            if isinstance(v, float):
                mask &= np.isclose(existing_outputs[k], v)
            else:
                mask &= existing_outputs[k] == v
        return not np.any(mask)
    else:
        return False


def get_cluster_name():
    result = str(subprocess.check_output(['qconf', '-ss']))
    if 'cluster-dev.ihme.washington.edu' in result:
        return 'dev'
    elif 'cluster-prod.ihme.washington.edu' in result:
        return 'prod'
    else:
        raise Exception('This script must be run on the IHME cluster')



def get_random_free_port():
    # NOTE: this implementation is vulnerable to rare race conditions where some other process gets the same
    # port after we free our socket but before we use the port number we got. Should be so rare in practice
    # that it doesn't matter.
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port
