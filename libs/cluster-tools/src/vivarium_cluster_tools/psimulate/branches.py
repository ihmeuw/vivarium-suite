"""
==============================
Branch and Keyspace Management
==============================

Tools for managing the parameter space of a parallel run.

"""
from itertools import product
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

import numpy as np
import yaml
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate.model_specification import FULL_ARTIFACT_PATH_KEY


class Keyspace:
    """A representation of a collection of simulation configurations."""

    def __init__(self, branches: List[Dict], keyspace: Dict):
        self.branches = branches
        self._keyspace = keyspace

    @classmethod
    def from_branch_configuration(
        cls, branch_configuration_file: Union[str, Path]
    ) -> "Keyspace":
        """
        Parameters
        ----------
        branch_configuration_file
            Absolute path to the branch configuration file.
        """
        (
            branches,
            input_draw_count,
            random_seed_count,
            input_draws,
            random_seeds,
        ) = load_branch_configuration(branch_configuration_file)
        keyspace = calculate_keyspace(branches)
        keyspace["input_draw"] = (
            input_draws if input_draws else calculate_input_draws(input_draw_count)
        )
        keyspace["random_seed"] = (
            random_seeds if random_seeds else calculate_random_seeds(random_seed_count)
        )
        return Keyspace(branches, keyspace)

    @classmethod
    def from_previous_run(cls, keyspace_path: Path, branches_path: Path) -> "Keyspace":
        keyspace = yaml.full_load(keyspace_path.read_text())
        branches = yaml.full_load(branches_path.read_text())
        return Keyspace(branches, keyspace)

    @classmethod
    def from_entry_point_args(
        cls,
        input_branch_configuration_path: Optional[Path],
        keyspace_path: Path,
        branches_path: Path,
        extras: Dict,
    ) -> "Keyspace":
        if input_branch_configuration_path is not None:
            keyspace = cls.from_branch_configuration(
                input_branch_configuration_path,
            )
        elif keyspace_path.exists():
            keyspace = cls.from_previous_run(keyspace_path, branches_path)
            keyspace.add_draws(extras.get("num_draws", 0))
            keyspace.add_seeds(extras.get("num_seeds", 0))
        else:
            keyspace = Keyspace([], {})

        return keyspace

    def persist(self, keyspace_path: Path, branches_path: Path) -> None:
        keyspace_path.write_text(yaml.dump(self._keyspace))
        branches_path.write_text(yaml.dump(self.branches))

    def add_draws(self, num_draws: int) -> None:
        existing = self._keyspace["input_draw"]
        additional = calculate_input_draws(num_draws, existing)
        self._keyspace["input_draw"] = existing + additional

    def add_seeds(self, num_seeds: int) -> None:
        existing = self._keyspace["random_seed"]
        additional = calculate_random_seeds(num_seeds, existing)
        self._keyspace["random_seed"] = existing + additional

    def __contains__(self, item: str) -> bool:
        """Checks whether the item is present in the Keyspace"""
        return item in self._keyspace

    def __iter__(self) -> Iterator[Tuple[int, int, Dict]]:
        """Yields and individual simulation configuration from the keyspace."""
        for job_config in product(
            self._keyspace["input_draw"], self._keyspace["random_seed"], self.branches
        ):
            if job_config[2] is None:
                job_config[2] = {}
            yield job_config

    def __len__(self) -> int:
        """Returns the number of individual simulation runs this keyspace represents."""
        return len(
            list(
                product(
                    self._keyspace["input_draw"], self._keyspace["random_seed"], self.branches
                )
            )
        )


def calculate_input_draws(
    input_draw_count: int, existing_draws: List[int] = None
) -> List[int]:
    """Determines a random sample of the GBD input draws to use given a draw
    count and any existing draws.

    Parameters
    ----------
    input_draw_count
        The number of draws to pull.

    existing_draws
        Any draws that have already been pulled and should not be pulled again.

    Returns
    -------
    List[int]
        A set of unique input draw numbers, guaranteed not to overlap with any
        existing draw numbers.

    """
    max_draw_count = 1000
    if input_draw_count > max_draw_count:
        raise ValueError(f"Input draw count must be less than {max_draw_count}.")

    possible = list(range(max_draw_count))
    if existing_draws:
        possible = sorted(list(set(possible).difference(existing_draws)))
        min_input_draw_count_allowed = 0
    else:
        min_input_draw_count_allowed = 1
    np.random.seed(123456)
    np.random.shuffle(possible)
    if min_input_draw_count_allowed <= input_draw_count <= len(possible):
        return possible[:input_draw_count]
    else:
        raise ValueError(
            f"Input draw count must be between {min_input_draw_count_allowed} "
            f"and {len(possible)} (inclusive). You specified {input_draw_count}."
        )


def calculate_random_seeds(
    random_seed_count: int, existing_seeds: List[int] = None
) -> List[int]:
    """Generates random seeds to use given a count of seeds and any existing
    seeds.

    Parameters
    ----------
    random_seed_count
        The number of random seeds to generate.

    existing_seeds
        Any random seeds that have already been generated and should not be
        generated again.

    Returns
    -------
    List[int]
        A set of unique random seeds, guaranteed not to overlap with any
        existing random seeds.

    """
    if not random_seed_count:
        return []

    max_seed_count = 10000
    if random_seed_count > max_seed_count:
        raise ValueError(f"Random seed count must be less than {max_seed_count}.")

    possible = list(range(max_seed_count))
    if existing_seeds:
        possible = sorted(list(set(possible).difference(existing_seeds)))
    np.random.seed(654321)
    np.random.shuffle(possible)
    return possible[:random_seed_count]


def calculate_keyspace(branches: List[Dict]) -> Dict:
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


def load_branch_configuration(
    path: Path,
) -> Tuple[List[Dict], int, int, Optional[List[int]], Optional[List[int]]]:
    data = yaml.full_load(path.read_text())

    input_draw_count = data.get("input_draw_count", 1)
    random_seed_count = data.get("random_seed_count", 1)
    input_draws = data.get("input_draws", None)
    random_seeds = data.get("random_seeds", None)

    # Validate configuration of counts and values for input_draws and random_seeds
    _check_count_and_values(
        data, input_draw_count, input_draws, "input_draw_count", "input_draws", 1000
    )
    _check_count_and_values(
        data,
        random_seed_count,
        random_seeds,
        "random_seed_count",
        "random_seeds",
        10000,
    )

    if "branches" in data:
        branches = expand_branch_templates(data["branches"])
    else:
        branches = [{}]

    return branches, input_draw_count, random_seed_count, input_draws, random_seeds


def _check_count_and_values(
    configuration: Dict,
    value_count: int,
    values: List[int],
    count_name: str,
    values_name: str,
    max_count: int,
):
    """Checks input configuration count and values for integers outside of range.

    Parameters
    ----------
    configuration
        Dictionary of the configuration data.
    value_count
        Integer for the number of values provided.
    values
        List of integer values.
    count_name
        Configuration key string for value count.
    values_name
        Configuration key string for values list.
    max_count
        Integer for the maximum number of values, maximum value is max_count - 1.
    """
    if count_name in configuration and values_name in configuration:
        if len(values) != value_count:
            raise ValueError(
                f"Both {count_name} and {values_name} are defined but they are inconsistent. "
                f"{count_name} is {value_count} while {values_name} has length {len(values)}. "
            )
    if values:
        if [d for d in values if d not in range(0, max_count)]:
            raise ValueError(
                f"{values_name} contains draws outside of 0-{max_count - 1}: "
                f"{[d for d in values if d not in range(0, max_count)]}"
            )
    if value_count < 1 or value_count > max_count:
        raise ValueError(f"{count_name} must be within 1-{max_count}. Given: {value_count}")


def expand_branch_templates(templates: Dict) -> List[Dict]:
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
                    i = pointers[k] + 1
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
            *ks, k = k.split(".")
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
