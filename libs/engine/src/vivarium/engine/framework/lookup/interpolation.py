"""
=============
Interpolation
=============

Provides interpolation algorithms across tabular data for ``vivarium``
simulations.

"""
from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Any, ClassVar, NamedTuple, TypeGuard

import numpy as np
import numpy.typing as npt
import pandas as pd

from vivarium.engine.types import LookupTableData


def has_named_row_index(
    data: LookupTableData,
) -> TypeGuard[pd.DataFrame | pd.Series[Any]]:
    """Return True if ``data`` carries its lookup attributes on the row index."""
    if isinstance(data, pd.Series):
        return True
    if isinstance(data, pd.DataFrame):
        return any(name is not None for name in data.index.names)
    return False


_START_SUFFIX = "_start"
_END_SUFFIX = "_end"


def _edge_columns(parameter: str) -> tuple[str, str]:
    """Get the left- and right-edge column names for a continuous parameter."""
    return f"{parameter}{_START_SUFFIX}", f"{parameter}{_END_SUFFIX}"


def _get_bin_edge_columns(continuous_parameters: Sequence[str]) -> list[str]:
    """Get the column names for the left and right edges of bins for each continuous parameter."""
    return [
        column for parameter in continuous_parameters for column in _edge_columns(parameter)
    ]


class _ParameterBins(NamedTuple):
    """Shared bin edges for one continuous parameter."""

    left_edges: npt.NDArray[Any]
    """Ascending unique left bin edges."""
    max_right: Any
    """Maximum right bin edge (the exclusive upper bound of the last bin)."""


class Interpolation:
    """A callable that interpolates value columns over categorical/continuous parameters.

    Lookup attributes are inferred from the input data: when the data has its
    lookup attributes in the row index (see :func:`has_named_row_index`), the
    row-index level names are the attributes; when the data is a flat
    DataFrame (deprecated), the attributes are the columns not listed in
    ``value_columns``. Attributes whose names follow the ``<name>_start`` /
    ``<name>_end`` convention are paired up as continuous binned parameters;
    all other attributes are treated as categorical key columns.

    This naming convention is the only way ``Interpolation`` distinguishes
    continuous from categorical parameters, so the class is not fully generic
    — it is the interpolation primitive that
    :class:`~vivarium.engine.framework.lookup.table.LookupTable` is built on.

    For order 0 (currently the only supported order) a call resolves each interpolant to
    the bin its continuous parameters fall in and returns that bin's values.
    The lookup is fully vectorized: a single :func:`numpy.digitize` pass per
    continuous parameter maps every interpolant to a bin, and a single
    :meth:`pandas.DataFrame.merge` keyed on the categorical columns plus each
    parameter's left bin edge retrieves the values for the whole population at
    once. This requires that the key columns uniquely identify a data row and
    that the bin edges are identical across every categorical group; both are
    validated on construction when ``validate`` is set. With ``validate=False``
    violations surface as a ``KeyError`` or ``pandas.errors.MergeError`` on
    call.

    """

    _FLAT_COLUMN_PREFIX: ClassVar[str] = "__lookup_col_"
    """Prefix for opaque internal value-column IDs used in interpolation."""

    def __init__(
        self,
        data: pd.DataFrame | pd.Series[Any],
        value_columns: pd.Index[Any],
        order: int,
        extrapolate: bool,
        validate: bool,
    ):
        # TODO: allow for order 1 interpolation with binned edges
        if order != 0:
            raise NotImplementedError(
                f"Interpolation is only supported for order 0. You specified order {order}"
            )

        self.value_columns: pd.Index[Any] = value_columns
        """User-facing column labels (including tuple labels from a column
        ``MultiIndex`` and ``None`` from a nameless Series) restored on the
        output of :meth:`__call__`."""
        self._internal_value_columns = [
            f"{self._FLAT_COLUMN_PREFIX}{i}" for i in range(len(value_columns))
        ]
        """Opaque internal column IDs used in the interpolation pipeline."""
        parameter_columns = (
            list(data.index.names)
            if has_named_row_index(data)
            else [c for c in data.columns if c not in value_columns]
        )
        self.continuous_parameters: list[str] = self._get_continuous_parameters(
            parameter_columns
        )
        """Lookup attributes used as binned ranges. The base name (e.g.
        ``"age"``) of each ``<name>_start`` / ``<name>_end`` pair."""
        self.categorical_parameters: list[str] = self._get_categorical_parameters(
            parameter_columns
        )
        """Lookup attributes used to select between value rows."""
        self.data: pd.DataFrame = self._reshape_data(data, value_columns)
        """Flat DataFrame the interpolation pipeline operates on. Value
        columns are renamed to opaque internal IDs (see ``_FLAT_COLUMN_PREFIX``);
        :attr:`value_columns` carries the original user-facing labels and is
        reapplied to the output of :meth:`__call__`."""

        self.order: int = order
        """Order of interpolation. Only ``0`` is currently supported."""
        self.extrapolate: bool = extrapolate
        """Whether to extrapolate beyond the edges of the supplied bins."""
        self.validate: bool = validate
        """Whether to validate inputs on construction and on call."""

        self._parameter_bins: dict[str, _ParameterBins] = {}
        """Shared per-continuous-parameter bin edges used by every interpolant,
        keyed by continuous-parameter base name."""
        for parameter in self.continuous_parameters:
            start_column, end_column = _edge_columns(parameter)
            left_edges = self.data[start_column].drop_duplicates().sort_values()
            self._parameter_bins[parameter] = _ParameterBins(
                left_edges=left_edges.to_numpy(),
                max_right=self.data[end_column].drop_duplicates().max(),
            )
        self._start_columns: list[str] = [
            _edge_columns(p)[0] for p in self.continuous_parameters
        ]
        self._key_columns: list[str] = list(self.categorical_parameters) + self._start_columns
        # With no key columns there is nothing to merge against: __call__
        # broadcasts the single data row directly.
        self._merge_target: pd.DataFrame | None = (
            self.data[self._key_columns + self._internal_value_columns]
            if self._key_columns
            else None
        )

        if validate:
            self._validate()

    @staticmethod
    def _get_continuous_parameters(parameter_columns: list[str]) -> list[str]:
        """Get continuous parameter columns from the given list of parameter columns."""
        parameter_columns_set = set(parameter_columns)
        continuous_columns: list[str] = []
        for column in parameter_columns:
            if str(column).endswith(_START_SUFFIX):
                base = str(column).removesuffix(_START_SUFFIX)
                if f"{base}{_END_SUFFIX}" in parameter_columns_set:
                    continuous_columns.append(base)
        return continuous_columns

    def _get_categorical_parameters(self, parameter_columns: list[str]) -> list[str]:
        """Get categorical parameter columns from the given list of parameter columns."""
        bin_edge_columns = set(_get_bin_edge_columns(self.continuous_parameters))
        return [col for col in parameter_columns if col not in bin_edge_columns]

    def _reshape_data(
        self,
        raw_data: pd.DataFrame | pd.Series[Any],
        returned_columns: pd.Index[Any],
    ) -> pd.DataFrame:
        """Get the flat representation of ``data`` for interpolation."""
        if has_named_row_index(raw_data):
            if isinstance(raw_data, pd.Series):
                flat = raw_data.to_frame(name=raw_data.name)
            else:
                flat = raw_data.copy(deep=False)
            flat.columns = pd.Index(self._internal_value_columns)
            return flat.reset_index()

        # This is the deprecated path where the input data is already in flat form
        assert isinstance(raw_data, pd.DataFrame)  # only Series/indexed paths above
        return raw_data.rename(
            columns=dict(zip(list(returned_columns), self._internal_value_columns))
        )

    def _validate(self) -> None:
        """Validate that the source data supports the single-merge lookup."""
        validate_parameters(
            self.data,
            self.categorical_parameters,
            self.continuous_parameters,
            self._internal_value_columns,
        )

        if self._key_columns:
            duplicated = self.data.duplicated(subset=self._key_columns, keep=False)
            if duplicated.any():
                duplicate_keys = self.data.loc[
                    duplicated, self._key_columns
                ].drop_duplicates()
                raise ValueError(
                    f"Interpolation data rows must be uniquely identified by the "
                    f"key columns {self._key_columns}, but multiple rows share "
                    f"these keys:\n{duplicate_keys.to_string(index=False)}"
                )
        elif len(self.data) > 1:
            raise ValueError(
                f"Interpolation data with no categorical or continuous parameters "
                f"must be a single row. You provided {len(self.data)} rows."
            )

        if not self.continuous_parameters:
            return

        # Validate completeness one categorical group at a time: on the full
        # multi-group table the duplicate-bin guard would trip on the repeated bins.
        groups = (
            [group for _, group in self.data.groupby(self.categorical_parameters)]
            if self.categorical_parameters
            else [self.data]
        )
        for group in groups:
            check_data_complete(group, self.continuous_parameters)

        for parameter in self.continuous_parameters:
            bins = self._parameter_bins[parameter]
            reference_edges = set(bins.left_edges)
            start_column, end_column = _edge_columns(parameter)
            for group in groups:
                if (
                    set(group[start_column]) != reference_edges
                    or group[end_column].max() != bins.max_right
                ):
                    raise ValueError(
                        f"Continuous parameter '{parameter}' has different bin edges "
                        f"across categorical groups. The vectorized single-merge "
                        f"lookup requires uniform bin edges across all categorical "
                        f"groups."
                    )

    def __call__(self, interpolants: pd.DataFrame) -> pd.DataFrame:
        """Get the interpolated results for the parameters in interpolants.

        Runs one :func:`numpy.digitize` per continuous parameter over the whole
        population to resolve each interpolant's bin, then performs a single
        left :meth:`pandas.DataFrame.merge` keyed on the categorical columns
        plus each parameter's left bin edge.

        Parameters
        ----------
        interpolants
            Data frame containing the parameters to interpolate.

        Returns
        -------
            A table with the interpolated values for the given interpolants,
            indexed by ``interpolants.index`` with columns
            :attr:`value_columns`.

        Raises
        ------
        ValueError
            If ``extrapolate`` is off and an interpolant falls outside the bins.
        KeyError
            If an interpolant carries a categorical value not present in the
            source data.
        """
        if self.validate:
            validate_call_data(
                interpolants, self.categorical_parameters, self.continuous_parameters
            )

        if interpolants.empty:
            return pd.DataFrame(
                index=interpolants.index, columns=self.value_columns, dtype=np.float64
            )

        original_index = interpolants.index

        if self._merge_target is None:
            # No categorical or continuous parameters: broadcast the first row.
            first_row = self.data[self._internal_value_columns].iloc[0]
            broadcast = pd.DataFrame(
                {column: first_row[column] for column in self._internal_value_columns},
                index=original_index,
            )
            broadcast.columns = self.value_columns
            return broadcast

        key_frame = pd.DataFrame(index=original_index)
        for column in self.categorical_parameters:
            key_frame[column] = interpolants[column].to_numpy()
        for parameter, start_column in zip(self.continuous_parameters, self._start_columns):
            bins = self._parameter_bins[parameter]
            values = interpolants[parameter]
            if not self.extrapolate and (
                values.min() < bins.left_edges[0] or values.max() >= bins.max_right
            ):
                raise ValueError(
                    f"Extrapolation outside the provided bins is disabled, but "
                    f"parameter '{parameter}' has values outside its bin range "
                    f"[{bins.left_edges[0]}, {bins.max_right})."
                )
            # Left edge inclusive, right exclusive; out-of-range values fold to
            # the nearest edge bin (below the minimum -> first, at/above the
            # maximum -> last).
            positions = np.digitize(values, bins.left_edges)
            positions[positions > 0] -= 1
            key_frame[start_column] = bins.left_edges[positions]

        merged = key_frame.merge(
            self._merge_target,
            how="left",
            on=self._key_columns,
            validate="many_to_one",
            indicator=True,
        )
        # A left many-to-one merge preserves left-row order, so the merged rows
        # line up positionally with the interpolants.
        merged.index = original_index

        # Raise if interpolant's key is missing from the source
        if self.categorical_parameters:
            unmatched = merged["_merge"].to_numpy() == "left_only"
            if unmatched.any():
                unknown = merged.loc[unmatched, self.categorical_parameters].drop_duplicates()
                raise KeyError(
                    f"Interpolants carry categorical values absent from the "
                    f"interpolation data (or, with validate=False, bin edges that "
                    f"differ across categorical groups):\n"
                    f"{unknown.to_string(index=False)}"
                )

        result = merged[self._internal_value_columns]
        result.columns = self.value_columns
        return result

    def __repr__(self) -> str:
        return "Interpolation()"


def validate_parameters(
    data: pd.DataFrame,
    categorical_parameters: Sequence[str],
    continuous_parameters: Sequence[str],
    value_columns: Sequence[Hashable],
) -> None:
    if data.empty:
        raise ValueError("You must supply non-empty data to create the interpolation.")

    if not value_columns:
        raise ValueError(
            f"No non-parameter data. Available columns: {data.columns}, "
            f"Parameter columns: {set(categorical_parameters) | set(continuous_parameters)}"
        )

    required_cols = {
        *categorical_parameters,
        *_get_bin_edge_columns(continuous_parameters),
        *value_columns,
    }
    if extra_columns := list(data.columns.difference(list(required_cols))):
        raise ValueError(
            "Data contains extra columns not in key_columns, parameter_columns, or "
            f"value_columns: {extra_columns}"
        )


def validate_call_data(
    data: pd.DataFrame,
    categorical_parameters: Sequence[str],
    continuous_parameters: Sequence[str],
) -> None:
    if not isinstance(data, pd.DataFrame):
        raise TypeError(
            f"Interpolations can only be called on pandas.DataFrames. You"
            f"passed {type(data)}."
        )

    if not set(continuous_parameters) <= set(data.columns.values.tolist()):
        raise ValueError(
            f"The continuous continuous parameters with which you built the Interpolation must all "
            f"be present in the data you call it on. The Interpolation has key "
            f"columns: {continuous_parameters} and your data has columns: "
            f"{data.columns.values.tolist()}"
        )

    if categorical_parameters and not set(categorical_parameters) <= set(
        data.columns.values.tolist()
    ):
        raise ValueError(
            f"The key (categorical) columns with which you built the Interpolation must all"
            f"be present in the data you call it on. The Interpolation has key"
            f"columns: {categorical_parameters} and your data has columns: "
            f"{data.columns.values.tolist()}"
        )


def check_data_complete(data: pd.DataFrame, continuous_parameters: Sequence[str]) -> None:
    """Check that data provides complete, contiguous bins for each continuous parameter.

    For each parameter (given by base name, with ``<name>_start`` /
    ``<name>_end`` edge columns), require that every combination of parameter
    bins is present, that each left edge pairs with exactly one right edge,
    and that the bins tile a continuous range: each bin's exclusive right edge
    equals the next bin's inclusive left edge.

    Raises
    ------
    ValueError
        If bins are duplicated or overlap, or if a combination of continuous
        parameters is missing.
    NotImplementedError
        If a parameter contains non-continuous bins.
    """
    start_columns = [_edge_columns(p)[0] for p in continuous_parameters]

    # A bin repeated for the same combination of the other parameters is an
    # overlap of itself.
    if data.duplicated(subset=start_columns).any():
        raise ValueError(
            f"Parameter data must not contain overlaps. Data contains duplicate "
            f"bins for {_get_bin_edge_columns(continuous_parameters)}."
        )

    for parameter in continuous_parameters:
        start_column, end_column = _edge_columns(parameter)
        other_start_columns = [c for c in start_columns if c != start_column]

        if (
            other_start_columns
            and (
                data.groupby(other_start_columns)[start_column].nunique()
                < data[start_column].nunique()
            ).any()
        ):
            raise ValueError(
                f"You must provide a value for every combination of "
                f"{list(continuous_parameters)}."
            )

        if (data.groupby(start_column)[end_column].nunique() > 1).any():
            raise ValueError(
                f"Parameter data must not contain overlaps. Parameter "
                f"('{start_column}', '{end_column}') contains overlapping data."
            )

        bins = data[[start_column, end_column]].drop_duplicates().sort_values(start_column)
        previous_end = bins[end_column].to_numpy()[:-1]
        next_start = bins[start_column].to_numpy()[1:]
        if (previous_end > next_start).any():
            raise ValueError(
                f"Parameter data must not contain overlaps. Parameter "
                f"('{start_column}', '{end_column}') contains overlapping data."
            )
        if (previous_end < next_start).any():
            raise NotImplementedError(
                f"Interpolation only supported for continuous parameters with "
                f"continuous bins. Parameter ('{start_column}', '{end_column}') "
                f"contains non-continuous bins."
            )
