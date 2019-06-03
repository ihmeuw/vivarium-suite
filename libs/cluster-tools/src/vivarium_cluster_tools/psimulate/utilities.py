from bdb import BdbQuit
from datetime import datetime
import functools
import math
import os
from pathlib import Path
import sys

from loguru import logger
from typing import Dict, List, Tuple
# depending on version of pip, freeze may be in one of two places
try:
    from pip._internal.operations import freeze
except ImportError:
    from pip.operations import freeze

from vivarium_cluster_tools.psimulate import globals as vct_globals


def get_drmaa():
    try:
        import drmaa
    except (RuntimeError, OSError):
        if 'SGE_CLUSTER_NAME' in os.environ:
            sge_cluster_name = os.environ['SGE_CLUSTER_NAME']
            if sge_cluster_name == "cluster":  # new cluster
                os.environ['DRMAA_LIBRARY_PATH'] = '/opt/sge/lib/lx-amd64/libdrmaa.so'
            else:  # old cluster - dev or prod
                os.environ['DRMAA_LIBRARY_PATH'] = f'/usr/local/UGE-{sge_cluster_name}/lib/lx-amd64/libdrmaa.so'
            import drmaa
        else:
            drmaa = object()
    return drmaa


def add_logging_sink(sink, verbose, colorize=False, serialize=False):
    message_format = ('<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | '
                      '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> '
                      '- <level>{message}</level>')
    if verbose == 0:
        def quiet_filter(record):
            return record.get('extra', {}).get('queue', None) == 'all'

        logger.add(sink, colorize=colorize, level="INFO", format=message_format,
                   filter=quiet_filter, serialize=serialize)
    elif verbose == 1:
        logger.add(sink, colorize=colorize, level="INFO", format=message_format, serialize=serialize)
    elif verbose >= 2:
        logger.add(sink, colorize=colorize, level="DEBUG", format=message_format, serialize=serialize)


def configure_master_process_logging_to_terminal(verbose):
    logger.remove()  # Clear default configuration
    add_logging_sink(sys.stdout, verbose, colorize=True)


def configure_master_process_logging_to_file(output_directory):
    master_log = output_directory / 'master.log'
    serial_log = output_directory / 'master.log.json'
    add_logging_sink(master_log, verbose=2)
    add_logging_sink(serial_log, verbose=2, serialize=True)


def get_output_directory(model_specification_file=None, output_directory=None, restart=False):
    if restart:
        output_directory = Path(output_directory)
    else:
        root = Path(output_directory) if output_directory else Path(vct_globals.DEFAULT_OUTPUT_DIRECTORY)
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        model_specification_name = Path(model_specification_file).stem
        output_directory = root / model_specification_name / launch_time
    return output_directory


def setup_directories(model_specification_file, result_directory, restart, expand):
    output_directory = get_output_directory(model_specification_file, result_directory, restart)

    if restart and not expand:
        command = 'restart'
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    elif restart and expand:
        command = 'expand'
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    else:
        command = 'run'
        launch_time = output_directory.stem

    logging_directory = output_directory / "logs" / f'{launch_time}_{command}'

    logging_dirs = {'main': logging_directory,
                    'sge': logging_directory / 'sge_logs',
                    'worker': logging_directory / 'worker_logs'}

    output_directory.mkdir(exist_ok=True, parents=True)
    for d in logging_dirs.values():
        d.mkdir(parents=True)

    return output_directory, logging_dirs


def get_cluster_name():
    try:
        cluster_name = {'prod': 'cluster-prod',
                        'dev': 'cluster-dev',
                        'cluster': 'cluster-fair'}[os.environ['SGE_CLUSTER_NAME']]
    except KeyError:
        raise RuntimeError('This tool must be run from the IHME cluster.')
    return cluster_name


def get_valid_project(project, cluster):
    if cluster == 'cluster-dev':
        project = None
    else:
        if project not in vct_globals.CLUSTER_PROJECTS:
            raise RuntimeError(f"Script only for use with Simulation Science "
                               f"cluster projects: {vct_globals.CLUSTER_PROJECTS}.")
    return project


def get_uge_specification(peak_memory, project, job_name):
    cluster_name = get_cluster_name()
    project = get_valid_project(project, cluster_name)

    preamble = f'-w n -q all.q -l m_mem_free={peak_memory}G -N {job_name}'

    if cluster_name == "cluster-fair":
        preamble += " -l fthread=1"
    else:
        # Calculate slot count based on expected peak memory usage and 2g per slot
        num_slots = int(math.ceil(peak_memory / 2.5))
        preamble += f' -pe multi_slot {num_slots}'

    if project:
        preamble += f' -P {project}'

    return preamble


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def handle_exceptions(func, with_debugger):
    """Drops a user into an interactive debugger if func raises an error."""

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except (BdbQuit, KeyboardInterrupt):
            raise
        except Exception as e:
            logger.exception("Uncaught exception {}".format(e))
            if with_debugger:
                import pdb
                import traceback
                traceback.print_exc()
                pdb.post_mortem()
            else:
                raise

    return wrapped


def parse_package_version(s: str) -> Tuple[str, str]:
    if 'no version control' in s:  # installed from non-git controlled source code in editable mode
        s = s.split('(')[1].split(')')[0]  # yields <package>==<version>

    packaged = s.split('==')
    if len(packaged) == 2:  # not installed from source code, e.g., <package>==<version>
        package = packaged[0]
        version = packaged[1]
    else:  # installed from source code in editable mode so more parsing required
        sourced = s.split('=')
        package = sourced[-1]
        version = sourced[0].split('@')[-1].replace('#egg', '')  # version here is git hash
    return package, version


def convert_pip_list_to_dict(pf_list: List[str]) -> Dict:
    return {p: v for p, v in [parse_package_version(s) for s in pf_list]}


def compare_environments(current: Dict, original: Dict):
    differences = []

    current_packages = set(current.keys())
    original_packages = set(original.keys())

    new_packages = current_packages.difference(original_packages)
    if new_packages:
        differences.append(
            f'The current environment contains the following packages not present in the original '
            f'environment: {new_packages}.')

    missing_packages = original_packages.difference(current_packages)
    if missing_packages:
        differences.append(
            f'The current environment is missing the following packages present in the original '
            f'environment: {missing_packages}.')

    differing_versions = []
    for p in current_packages.intersection(original_packages):
        if current[p] != original[p]:
            differing_versions.append(f'{p}: {original[p]} -> {current[p]}')

    if differing_versions:
        differences.append(
            f'Different versions found in current and original environment for the following '
            f'packages: {differing_versions}.')

    if differences:
        differences = "\n".join(differences)
        raise ValueError(f'Differences found between environment used for original run and current environment. '
                         f'In order to successfully run, you should make a new environment using the requirements.txt '
                         f'file found in the output directory. Differences found as follows: {differences}.')


def validate_environment(output_dir: Path):
    original_environment_file = output_dir / 'requirements.txt'

    current_environment_list = [p for p in freeze.freeze()]
    if not original_environment_file.exists():  # original run
        with original_environment_file.open('w') as f:
            f.write('\n'.join(current_environment_list))
    else:  # compare with original
        with original_environment_file.open('r') as f:
            original_environment_list = [line.replace('\n', '') for line in f]
        current_environment = convert_pip_list_to_dict(current_environment_list)
        original_environment = convert_pip_list_to_dict(original_environment_list)
        compare_environments(current_environment, original_environment)
        logger.info('Validation of environment successful. All pip installed packages match '
                    'original versions. Run can proceed.')

