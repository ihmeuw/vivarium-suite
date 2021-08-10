"""
===================
psimulate Utilities
===================

Utilities for psimulate runs.

"""
from datetime import datetime
import os
from pathlib import Path
from shutil import rmtree
import sys
from typing import Dict, List, Tuple, Sequence

from loguru import logger


# depending on version of pip, freeze may be in one of two places
try:
    from pip._internal.operations import freeze
except ImportError:
    from pip.operations import freeze

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate import globals as vct_globals


def get_drmaa() -> object:
    try:
        import drmaa
    except (RuntimeError, OSError):
        if 'SGE_CLUSTER_NAME' in os.environ:
            os.environ['DRMAA_LIBRARY_PATH'] = '/opt/sge/lib/lx-amd64/libdrmaa.so'
            import drmaa
        else:
            drmaa = object()
    return drmaa


def add_logging_sink(sink, verbose: int, colorize=False, serialize=False):
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


def configure_master_process_logging_to_terminal(verbose: int):
    logger.remove(0)  # Clear default configuration
    add_logging_sink(sys.stdout, verbose, colorize=True)


def configure_master_process_logging_to_file(output_directory: Path):
    master_log = output_directory / 'master.log'
    serial_log = output_directory / 'master.log.json'
    add_logging_sink(master_log, verbose=2)
    add_logging_sink(serial_log, verbose=2, serialize=True)


def get_output_directory(model_specification_file: str = None,
                         output_directory: str = None, restart: bool = False) -> Path:
    if restart:
        output_directory = Path(output_directory)
    else:
        root = Path(output_directory) if output_directory else Path(vct_globals.DEFAULT_OUTPUT_DIRECTORY)
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        model_specification_name = Path(model_specification_file).stem
        output_directory = root / model_specification_name / launch_time
    return output_directory


def set_permissions(output_dir: Path):
    """Call to achieve side effect of relaxing permissions to 775
        on output dir and the parent"""
    permissions = 0o775
    output_dir.parent.chmod(permissions)
    output_dir.chmod(permissions)


def setup_directories(model_specification_file: str, result_directory: str,
                      restart: bool, expand: bool) -> Tuple[Path, Dict[str, Path]]:
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

    vct_utils.mkdir(output_directory, exists_ok=True, parents=True)
    for d in logging_dirs.values():
        vct_utils.mkdir(d, parents=True)

    return output_directory, logging_dirs


def get_hostname() -> str:
    return os.environ.get(vct_globals.CLUSTER_ENV_HOSTNAME)


def exit_if_on_submit_host(name: str):
    if vct_globals.SUBMIT_HOST_MARKER in name:
        raise RuntimeError('This tool must not be run from a submit host.')


def chunks(l: Sequence, n: int):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


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


def check_for_empty_results_dir(output_dir: Path):
    """Remove the results directory including runner and worker logs if the simulation produced no results (i.e.,
    it failed). """
    if not (output_dir / 'output.hdf').exists():
        rmtree(output_dir)
