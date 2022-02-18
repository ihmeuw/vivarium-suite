import pytest
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate.branches import (
    calculate_random_seeds,
    expand_branch_templates,
)


def test_expand_branch_template():
    source = [
        {"a": {"b": [1, 2], "c": 3, "d": [4, 5, 6], "e": [True, False]}},
        {"a": {"b": 10, "c": 30, "d": 40, "e": True}},
    ]
    result = expand_branch_templates(source)

    result = [collapse_nested_dict(r) for r in result]

    expected = [
        collapse_nested_dict(r)
        for r in [
            {"a": {"b": 1, "c": 3, "d": 4, "e": True}},
            {"a": {"b": 2, "c": 3, "d": 5, "e": True}},
            {"a": {"b": 1, "c": 3, "d": 6, "e": True}},
            {"a": {"b": 2, "c": 3, "d": 4, "e": True}},
            {"a": {"b": 1, "c": 3, "d": 5, "e": True}},
            {"a": {"b": 2, "c": 3, "d": 6, "e": True}},
            {"a": {"b": 10, "c": 30, "d": 40, "e": True}},
            {"a": {"b": 1, "c": 3, "d": 4, "e": False}},
            {"a": {"b": 2, "c": 3, "d": 5, "e": False}},
            {"a": {"b": 1, "c": 3, "d": 6, "e": False}},
            {"a": {"b": 2, "c": 3, "d": 4, "e": False}},
            {"a": {"b": 1, "c": 3, "d": 5, "e": False}},
            {"a": {"b": 2, "c": 3, "d": 6, "e": False}},
        ]
    ]
    assert sorted(result) == sorted(expected)


@pytest.mark.parametrize("seed_count", [0, 10, 100, 1000])
def test_calculate_random_seeds(seed_count):
    seeds = calculate_random_seeds(seed_count)
    assert len(seeds) == len(set(seeds))
    assert len(seeds) == seed_count


@pytest.mark.parametrize("seed_count", [0, 10, 100, 1000])
def test_calculate_random_seeds_existing(seed_count):
    existing = list(range(20))

    seeds = calculate_random_seeds(seed_count, existing)
    assert len(seeds) == len(set(seeds))
    assert len(seeds + existing) == len(set(seeds + existing))
    assert len(seeds) == seed_count
