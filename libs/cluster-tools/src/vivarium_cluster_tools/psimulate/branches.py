"""
==============================
Branch and Keyspace Management
==============================

Tools for managing the parameter space of a parallel run.

"""
from itertools import product

import yaml
import numpy as np
from pathlib import Path
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate import globals as vct_globals

FULL_ARTIFACT_PATH_KEY = f'{vct_globals.INPUT_DATA_KEY}.{vct_globals.ARTIFACT_PATH_KEY}'


class Keyspace:
    """A representation of a collection of simulation configurations."""

    def __init__(self, branches, keyspace):
        self.branches = branches
        self._keyspace = keyspace

    @classmethod
    def from_branch_configuration(cls, num_input_draws, num_random_seeds, branch_configuration_file):
        """
        Parameters
        ----------
        num_input_draws: int
            The number of GBD input draws to run.
        num_random_seeds: int
            The number of different random seeds to use for each GBD input draw.  Each model
            draw creates a simulation with fixed GBD data, but a different sample of the
            exogenous randomness used in the simulation.
        branch_configuration_file: str
            Absolute path to the branch configuration file.
        """
        branches, input_draw_count, random_seed_count = load_branches(num_input_draws,
                                                                      num_random_seeds,
                                                                      branch_configuration_file)
        keyspace = calculate_keyspace(branches)
        keyspace['input_draw'] = calculate_input_draws(input_draw_count)
        keyspace['random_seed'] = calculate_random_seeds(random_seed_count)

        return Keyspace(branches, keyspace)

    @classmethod
    def from_previous_run(cls, path: Path):
        keyspace = yaml.full_load((path / 'keyspace.yaml').read_text())
        branches = yaml.full_load((path / 'branches.yaml').read_text())
        return Keyspace(branches, keyspace)

    def get_data(self):
        """Returns a copy of the underlying keyspace data."""
        return self._keyspace.copy()

    def get_branch_number(self, branch):
        for (i, b) in enumerate(self.branches):
            if b == branch:
                return i
        raise KeyError(f"No matching branch {branch}")

    def persist(self, output_directory: Path):
        (output_directory / 'keyspace.yaml').write_text(yaml.dump(self.get_data()))
        (output_directory / 'branches.yaml').write_text(yaml.dump(self.branches))

    def add_draws(self, num_draws):
        existing = self._keyspace['input_draw']
        additional = calculate_input_draws(num_draws, existing)
        self._keyspace['input_draw'] = existing + additional

    def add_seeds(self, num_seeds):
        existing = self._keyspace['random_seed']
        additional = calculate_random_seeds(num_seeds, existing)
        self._keyspace['random_seed'] = existing + additional

    def __contains__(self, item):
        """Checks whether the item is present in the Keyspace"""
        return item in self._keyspace

    def __iter__(self):
        """Yields and individual simulation configuration from the keyspace."""
        for job_config in product(self._keyspace['input_draw'], self._keyspace['random_seed'], self.branches):
            yield job_config

    def __len__(self):
        """Returns the number of individual simulation runs this keyspace represents."""
        return len(list(product(self._keyspace['input_draw'], self._keyspace['random_seed'], self.branches)))


def calculate_input_draws(input_draw_count, existing_draws=None):
    """Determines a random sample of the GBD input draws to use given a draw
    count and any existing draws.

    Parameters
    ----------
    input_draw_count: int
        The number of draws to pull.

    existing_draws: list
        Any draws that have already been pulled and should not be pulled again.

    Returns
    -------
    Iterable:
        A set of unique input draw numbers, guaranteed not to overlap with any
        existing draw numbers.
    """
    np.random.seed(123456)
    if existing_draws:
        possible = list(set(range(1000)).difference(existing_draws))
        min_input_draw_count_allowed = 0
    else:
        possible = list(range(1000))
        min_input_draw_count_allowed = 1

    if min_input_draw_count_allowed <= input_draw_count <= len(possible):
        return np.random.choice(possible, input_draw_count, replace=False).tolist()
    else:
        raise ValueError(f"Input draw count must be between {min_input_draw_count_allowed} "
                         f"and {len(possible)} (inclusive). You specified {input_draw_count}.")


def calculate_random_seeds(random_seed_count, existing_seeds=None):
    """Generates random seeds to use given a count of seeds and any existing
    seeds.

    Parameters
    ----------
    random_seed_count: int
        The number of random seeds to generate.

    existing_seeds: list
        Any random seeds that have already been generated and should not be
        generated again.

    Returns
    -------
    Iterable:
        A set of unique random seeds, guaranteed not to overlap with any
        existing random seeds.
    """
    if not random_seed_count:
        return []

    np.random.seed(654321)

    if existing_seeds:
        min_possible = max(existing_seeds) + 1
    else:
        existing_seeds = []
        min_possible = 0

    low, high = min_possible, min_possible + 10*random_seed_count
    possible = list(range(low, high))
    return np.random.choice(possible, random_seed_count, replace=False).tolist()


def calculate_keyspace(branches):
    if branches[0] is None:
        return {}

    keyspace = {k: {v} for k, v in collapse_nested_dict(branches[0])}

    for branch in branches[1:]:
        branch = dict(collapse_nested_dict(branch))
        if set(branch.keys()) != set(keyspace.keys()):
            raise ValueError("All branches must have the same keys")
        for k, v in branch.items():
            if k == FULL_ARTIFACT_PATH_KEY:
                validate_artifact_path(v)
            keyspace[k].add(v)
    keyspace = {k: list(v) for k, v in keyspace.items()}
    return keyspace


def load_branches(num_input_draws, num_random_seeds, branch_configuration_file):
    if num_input_draws is None and num_random_seeds is None and branch_configuration_file is not None:
        input_draw_count, random_seed_count, branches = load_branch_configurations(branch_configuration_file)
    elif num_input_draws is not None and branch_configuration_file is None:
        input_draw_count = num_input_draws
        random_seed_count = num_random_seeds if num_random_seeds is not None else 1
        branches = [None]
    else:
        raise ValueError('Must supply one of branch_configuration_file or --num_input_draws but not both')

    return branches, input_draw_count, random_seed_count


def load_branch_configurations(path):
    with open(path) as f:
        data = yaml.full_load(f)

    input_draw_count = data.get('input_draw_count', 1)
    random_seed_count = data.get('random_seed_count', 1)

    assert input_draw_count <= 1000, "Cannot use more that 1000 draws from GBD"

    if 'branches' in data:
        branches = expand_branch_templates(data['branches'])
    else:
        branches = [None]

    return input_draw_count, random_seed_count, branches


def expand_branch_templates(templates):
    """
    Take a list of dictionaries of configuration values (like the ones used in
    experiment branch configurations) and expand it by taking any values which
    are lists and creating a new set of branches which is made up of the
    product of all those lists plus all non-list values.

    For example this:

    .. code::

       {'a': {'b': [1,2], 'c': 3, 'd': [4,5,6]}}

    becomes this:

    .. code::

       [
           {'a': {'b': 1, 'c': 3, 'd': 4}},
           {'a': {'b': 2, 'c': 3, 'd': 5}},
           {'a': {'b': 1, 'c': 3, 'd': 6}},
           {'a': {'b': 2, 'c': 3, 'd': 4}},
           {'a': {'b': 1, 'c': 3, 'd': 5}},
           {'a': {'b': 2, 'c': 3, 'd': 6}}
       ]

    """
    expanded_branches = []

    for branch in templates:
        branch = sorted(collapse_nested_dict(branch))
        branch = [(k, v if isinstance(v, list) else [v]) for k, v in branch]
        expanded_size = np.product([len(v) for k, v in branch])
        new_branches = []
        pointers = {k: 0 for k, _ in branch}
        for _ in range(expanded_size):
            new_branch = []
            tick = True
            for k, v in branch:
                new_branch.append((k, v[pointers[k]]))
                if tick:
                    i = pointers[k]+1
                    if i < len(v):
                        tick = False
                        pointers[k] = i
                    else:
                        pointers[k] = 0
            new_branches.append(new_branch)
        expanded_branches.extend(new_branches)

    final_branches = []
    for branch in expanded_branches:
        root = {}
        final_branches.append(root)
        for k, v in branch:
            current = root
            *ks, k = k.split('.')
            for sub_k in ks:
                if sub_k in current:
                    current = current[sub_k]
                else:
                    current[sub_k] = {}
                    current = current[sub_k]
            current[k] = v

    return final_branches


def validate_artifact_path(artifact_path: str) -> str:
    """Validates that the path to the data artifact from the branches file exists.

    The path specified in the configuration must be absolute

    Parameters
    ----------
    artifact_path
        The path to the artifact.

    """
    path = Path(artifact_path)

    if not path.is_absolute() or not path.exists():
        raise FileNotFoundError(f"Cannot find artifact at path {path}")

    return str(path)
