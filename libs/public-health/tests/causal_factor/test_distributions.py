"""Unit tests for the ensemble parameter consolidation helpers.

These cover :func:`combine_distribution_parameters` and
:func:`split_distribution_parameters`, which fold an ensemble's
per-distribution parameter frames into a single wide frame and back again.

The functions operate on a ``dict[str, pd.DataFrame]`` keyed by distribution
name. Each value is a parameter frame that shares the same demographic index
as its siblings but has its own (often overlapping) parameter columns -- for
example a ``"gamma"`` frame with columns ``["a", "scale", "x_min", "x_max"]``
and a ``"norm"`` frame with columns ``["loc", "scale", "x_min", "x_max"]``,
which collide on ``scale``/``x_min``/``x_max``.
"""

import pandas as pd

from vivarium.public_health.causal_factor.distributions import (
    PARAMETER_SEPARATOR,
    combine_distribution_parameters,
    split_distribution_parameters,
)


def _make_parameters() -> dict[str, pd.DataFrame]:
    """Build a small ensemble whose members share parameter names.

    Two members ("gamma" and "norm") sit on the same 2-row demographic index
    and collide on the "scale"/"x_min"/"x_max" parameter names, so namespacing
    is exercised.
    """
    index = pd.MultiIndex.from_tuples(
        [("Male", 1990), ("Female", 1990)],
        names=["sex", "year_start"],
    )
    gamma = pd.DataFrame(
        {
            "a": [1.0, 2.0],
            "scale": [3.0, 4.0],
            "x_min": [0.0, 0.1],
            "x_max": [10.0, 11.0],
        },
        index=index,
    )
    norm = pd.DataFrame(
        {
            "loc": [5.0, 6.0],
            "scale": [7.0, 8.0],
            "x_min": [0.2, 0.3],
            "x_max": [12.0, 13.0],
        },
        index=index,
    )
    return {"gamma": gamma, "norm": norm}


def test_combine_then_split_round_trips_parameters() -> None:
    """Splitting a combined frame recovers each member's original frame unchanged."""
    parameters = _make_parameters()

    combined, columns_by_distribution = combine_distribution_parameters(parameters)
    recovered = split_distribution_parameters(combined, columns_by_distribution)

    assert set(recovered) == set(parameters)
    for distribution, original in parameters.items():
        pd.testing.assert_frame_equal(recovered[distribution], original)


def test_combine_then_split_round_trips_single_member() -> None:
    """A one-member ensemble round-trips through combine and split unchanged."""
    parameters = {"gamma": _make_parameters()["gamma"]}

    combined, columns_by_distribution = combine_distribution_parameters(parameters)
    recovered = split_distribution_parameters(combined, columns_by_distribution)

    assert set(recovered) == {"gamma"}
    pd.testing.assert_frame_equal(recovered["gamma"], parameters["gamma"])


def test_combine_produces_unique_columns_when_members_share_parameter_names() -> None:
    """Members sharing a parameter name get distinct columns in the combined frame."""
    parameters = _make_parameters()

    combined, _ = combine_distribution_parameters(parameters)

    columns = list(combined.columns)
    assert len(columns) == len(set(columns))
    assert len(columns) == sum(frame.shape[1] for frame in parameters.values())


def test_combine_columns_are_namespaced_with_separator() -> None:
    """Each combined column is its distribution name and parameter joined by the separator."""
    parameters = _make_parameters()

    combined, _ = combine_distribution_parameters(parameters)

    expected_columns = {
        f"{distribution}{PARAMETER_SEPARATOR}{param}"
        for distribution, frame in parameters.items()
        for param in frame.columns
    }
    assert set(combined.columns) == expected_columns


def test_combine_preserves_shared_demographic_index() -> None:
    """The combined frame keeps the demographic index shared by the member frames."""
    parameters = _make_parameters()
    shared_index = parameters["gamma"].index

    combined, _ = combine_distribution_parameters(parameters)

    pd.testing.assert_index_equal(combined.index, shared_index)


def test_combine_represents_only_supplied_members() -> None:
    """Only members present in the input appear in the combined frame and column mapping."""
    parameters = _make_parameters()
    del parameters["norm"]

    combined, columns_by_distribution = combine_distribution_parameters(parameters)

    assert set(columns_by_distribution) == {"gamma"}
    assert not any(
        column.startswith(f"norm{PARAMETER_SEPARATOR}") for column in combined.columns
    )


def test_split_restores_original_parameter_names() -> None:
    """Split frames carry the original parameter column names, not the namespaced labels."""
    parameters = _make_parameters()

    combined, columns_by_distribution = combine_distribution_parameters(parameters)
    recovered = split_distribution_parameters(combined, columns_by_distribution)

    for distribution, original in parameters.items():
        assert list(recovered[distribution].columns) == list(original.columns)


def test_split_returns_one_frame_per_member() -> None:
    """The split result is keyed by exactly the supplied distribution names."""
    parameters = _make_parameters()

    combined, columns_by_distribution = combine_distribution_parameters(parameters)
    recovered = split_distribution_parameters(combined, columns_by_distribution)

    assert set(recovered) == set(parameters)
