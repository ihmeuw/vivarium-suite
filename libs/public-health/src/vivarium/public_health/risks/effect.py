"""
==================
Risk Effect Models
==================

This module contains tools for modeling the relationship between risk
exposure models and disease models.

"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
import scipy
from vivarium.config_tree import ConfigTree
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.lookup import LookupTable

from vivarium.public_health.causal_factor.distributions import MissingDataError
from vivarium.public_health.causal_factor.effect import CausalFactorEffect
from vivarium.public_health.risks import Risk
from vivarium.public_health.utilities import EntityString, TargetString


class RiskEffect(CausalFactorEffect):
    """A component to model the effect of a risk factor on an affected entity's target rate.

    This component can source data either from builder.data or from parameters
    supplied in the configuration.

    For a risk named 'risk' that affects  'affected_risk' and 'affected_cause',
    the configuration would look like:

    .. code-block:: yaml

       configuration:
            risk_effect.risk_name_on_affected_target:
               exposure_parameters: 2
               incidence_rate: 10

    """

    EXPOSURE_CLASS = Risk

    ##############
    # Properties #
    ##############

    @staticmethod
    def get_name(risk: EntityString, target: TargetString) -> str:
        """The name of this risk effect component."""
        return f"risk_effect.{risk.name}_on_{target}"

    @property
    def risk(self) -> str:
        """The type and name of a risk, specified as "type.name". Type is singular."""
        return self.causal_factor

    #####################
    # Lifecycle methods #
    #####################

    def __init__(self, risk: str, target: str):
        """

        Parameters
        ----------
        risk
            Type and name of risk factor, supplied in the form
            "risk_type.risk_name" where risk_type should be singular (e.g.,
            risk_factor instead of risk_factors).
        target
            Type, name, and target rate of entity to be affected by risk factor,
            supplied in the form "entity_type.entity_name.measure"
            where entity_type should be singular (e.g., cause instead of causes).
        """
        super().__init__(risk, target)


class NonLogLinearRiskEffect(RiskEffect):
    """A component to model the exposure-parametrized effect of a risk factor.

    More specifically, this models the effect of the risk factor on the target rate of
    some affected entity.

    This component:

    1. Reads TMRED data from its configured data source (the artifact by
       default) and defines the TMREL.
    2. Calculates the relative risk at TMREL by linearly interpolating over
       relative risk data defined in the configuration.
    3. Divides relative risk data from configuration by RR at TMREL
       and clips to :attr:`MINIMUM_RELATIVE_RISK`.
    4. Builds a ``LookupTable`` that returns the exposure and RR of the left
       and right edges of the RR bin containing a simulant's exposure.
    5. Uses this ``LookupTable`` to modify the target pipeline by linearly
       interpolating a simulant's RR value and multiplying it by the intended
       target rate.
    """

    MINIMUM_RELATIVE_RISK: float | None = 1.0
    """Lower bound applied to the TMREL-normalized relative risks. Set to
    ``None`` for a risk that is protective over part of its exposure range and
    so needs to keep relative risks below 1."""

    ##############
    # Properties #
    ##############

    @property
    def exposure_column_name(self) -> str:
        """Name of the state table column this effect reads exposure from.

        Mirrors :attr:`vivarium.public_health.risks.base_risk.Risk.exposure_column_name`;
        the exposure is read from the state table rather than the exposure
        pipeline because it is also a lookup key of the RR table.
        """
        return f"{self.causal_factor.name}_exposure_for_non_loglinear_riskeffect"

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        """Default configuration values for this component.

        Configuration structure::

            {risk_effect_name}:
                data_sources:
                    relative_risk:
                        Source for relative risk data. Default is the artifact
                        key ``{risk}.relative_risk``. The data must be a
                        DataFrame with a numeric ``parameter`` column containing
                        exposure thresholds and a ``value`` column with the
                        corresponding relative risks.
                    population_attributable_fraction:
                        Source for PAF data. Default is the artifact key
                        ``{risk}.population_attributable_fraction``. Used to
                        adjust the target rate to account for the portion
                        attributable to this risk.
                    tmred:
                        Source for theoretical-minimum-risk exposure (TMRED)
                        data, used to compute the TMREL at which the relative
                        risk is normalized to 1. Default is the artifact key
                        ``{risk}.tmred``. Accepts a single-row DataFrame with
                        ``distribution``, ``min``, and ``max`` columns to
                        bypass the artifact. The ``distribution`` column must be
                        one of ``"uniform"`` (TMREL drawn uniformly from
                        ``[min, max]``) or ``"draws"`` (draw-level TMRELs).
        """
        return {
            self.name: {
                "data_sources": {
                    "relative_risk": f"{self.causal_factor}.relative_risk",
                    "population_attributable_fraction": f"{self.causal_factor}.population_attributable_fraction",
                    "tmred": f"{self.causal_factor}.tmred",
                },
            }
        }

    #################
    # Setup methods #
    #################

    @property
    def name(self) -> str:
        """The name of this non-log-linear risk effect component."""
        return f"non_log_linear_risk_effect.{self.causal_factor.name}_on_{self.target}"

    def build_rr_lookup_table(self, builder: Builder) -> LookupTable:
        """Build a lookup table mapping exposure intervals to relative risks.

        Define left and right edges of exposure bins and their
        corresponding relative risk values for piecewise linear
        interpolation.

        Parameters
        ----------
        builder
            Access point for utilizing framework interfaces during setup.

        Returns
        -------
            A lookup table with columns for left/right exposure and
            left/right relative risk values.
        """
        rr_data = self.load_relative_risk(builder)
        self.validate_rr_data(rr_data)

        demographic_cols = self.get_demographic_columns(rr_data)
        rr_data = (
            rr_data.groupby(demographic_cols)
            .apply(self.define_rr_intervals, include_groups=False)
            .reset_index(level=-1, drop=True)
            .reset_index()
        )
        rr_data = rr_data.drop("parameter", axis=1)
        rr_data[f"{self.exposure_column_name}_start"] = rr_data["left_exposure"]
        rr_data[f"{self.exposure_column_name}_end"] = rr_data["right_exposure"]

        rr_value_cols = ["left_exposure", "left_rr", "right_exposure", "right_rr"]
        return self.build_lookup_table(
            builder, "relative_risk", data_source=rr_data, value_columns=rr_value_cols
        )

    def define_rr_intervals(self, group: pd.DataFrame) -> pd.DataFrame:
        """Build the exposure and RR interval columns for one demographic group.

        Each row becomes the bin spanning from the previous exposure threshold
        to its own, carrying the RR at both edges so
        :meth:`get_relative_risk_source` can interpolate within the bin. A
        final open-ended bin is appended above the highest threshold, holding
        the RR flat.

        Parameters
        ----------
        group
            One demographic group's RR curve, with a ``parameter`` column of
            ascending exposure thresholds and a ``value`` column of RRs.

        Returns
        -------
            The group's bins, with ``left_exposure``, ``left_rr``,
            ``right_exposure``, and ``right_rr`` columns.
        """
        # The right-most bin is unbounded above and holds the last RR.
        max_exposure_row = group.tail(1).copy()
        max_exposure_row["parameter"] = np.inf
        rr_data = pd.concat([group, max_exposure_row]).reset_index()

        rr_data["left_exposure"] = [0.0] + rr_data["parameter"].iloc[:-1].tolist()
        rr_data["left_rr"] = [self.get_lowest_bin_left_rr(rr_data["value"])] + rr_data[
            "value"
        ].iloc[:-1].tolist()
        rr_data["right_exposure"] = rr_data["parameter"]
        rr_data["right_rr"] = rr_data["value"]

        return rr_data[
            ["parameter", "left_exposure", "left_rr", "right_exposure", "right_rr"]
        ]

    def get_lowest_bin_left_rr(self, rr_values: pd.Series[float]) -> float:
        """Return the RR at the left edge of the lowest exposure bin.

        That bin spans from zero up to the lowest exposure threshold, below the
        range the RR curve is defined over, so its left RR is extrapolated
        rather than read. The default is the smallest RR in the curve, which
        suits a monotonically increasing curve; override for a curve whose
        minimum sits elsewhere.

        Parameters
        ----------
        rr_values
            The group's RRs, ordered by ascending exposure.

        Returns
        -------
            The RR to assign below the lowest exposure threshold.
        """
        return float(rr_values.min())

    def load_relative_risk(
        self,
        builder: Builder,
        configuration: ConfigTree | None = None,
    ) -> str | float | pd.DataFrame:
        """Load relative risk data, normalizing by RR at the TMREL.

        Compute the Theoretical Minimum-Risk Exposure Level (TMREL)
        from TMRED data, interpolate RR at the TMREL, divide all RR
        values by this quantity, and clip to be at least 1.

        Parameters
        ----------
        builder
            Access point for utilizing framework interfaces during setup.
        configuration
            Optional configuration override. If ``None``, use
            ``self.configuration``.

        Returns
        -------
            The normalized relative risk data as a DataFrame.

        Raises
        ------
        MissingDataError
            If the TMRED data uses draw-level TMRELs or is not found.
        ValueError
            If the relative risk data fails validation (e.g. it is empty,
            or its ``parameter`` column is non-numeric or not monotonically
            increasing). See :meth:`validate_rr_data`.
        """
        if configuration is None:
            configuration = self.configuration

        self.tmrel = self.get_tmrel(builder, configuration)

        original_rrs = self.get_filtered_data(
            builder, configuration.data_sources.relative_risk
        )
        self.validate_rr_data(original_rrs)

        demographic_cols = self.get_demographic_columns(original_rrs)

        def get_rr_at_tmrel(rr_data: pd.DataFrame) -> float:
            """Interpolate the relative risk at the TMREL."""
            interpolated_rr_function = scipy.interpolate.interp1d(
                rr_data["parameter"],
                rr_data["value"],
                kind="linear",
                bounds_error=False,
                fill_value=(
                    rr_data["value"].min(),
                    rr_data["value"].max(),
                ),
            )
            rr_at_tmrel = interpolated_rr_function(self.tmrel).item()
            return rr_at_tmrel

        rrs_at_tmrel = (
            original_rrs.groupby(demographic_cols)
            .apply(get_rr_at_tmrel, include_groups=False)
            .rename("rr_at_tmrel")
        )
        rr_data = original_rrs.merge(rrs_at_tmrel.reset_index())
        rr_data["value"] = rr_data["value"] / rr_data["rr_at_tmrel"]
        if self.MINIMUM_RELATIVE_RISK is not None:
            rr_data["value"] = np.clip(rr_data["value"], self.MINIMUM_RELATIVE_RISK, np.inf)
        rr_data = rr_data.drop("rr_at_tmrel", axis=1)

        return rr_data

    def get_tmrel(self, builder: Builder, configuration: ConfigTree | None = None) -> float:
        """Draw the Theoretical Minimum-Risk Exposure Level from the TMRED data.

        Parameters
        ----------
        builder
            Access point for utilizing framework interfaces during setup.
        configuration
            Optional configuration override. If ``None``, use
            ``self.configuration``.

        Returns
        -------
            The TMREL, drawn uniformly from the TMRED range.

        Raises
        ------
        MissingDataError
            If the TMRED data uses draw-level TMRELs or is not found.
        """
        tmred = self.get_tmred(builder, configuration)
        if tmred["distribution"] == "uniform":
            draw = builder.configuration.input_data.input_draw_number
            rng = np.random.default_rng(builder.randomness.get_seed(self.name + str(draw)))
            return float(rng.uniform(tmred["min"], tmred["max"]))
        if tmred["distribution"] == "draws":  # currently only for iron deficiency
            raise MissingDataError(
                f"This data has draw-level TMRELs. You will need to contact the research team that models {self.causal_factor.name} to get this data."
            )
        raise MissingDataError(
            f"No TMRED found in gbd_mapping for risk {self.causal_factor.name}"
        )

    @staticmethod
    def get_demographic_columns(rr_data: pd.DataFrame) -> list[str]:
        """Return the columns of ``rr_data`` that key the RR curve, i.e. all
        columns other than ``parameter`` and ``value``."""
        return [col for col in rr_data.columns if col not in ("parameter", "value")]

    def get_relative_risk_source(self, builder: Builder) -> Callable[[pd.Index], pd.Series]:
        """Build a callable that interpolates relative risk from exposure.

        Use piecewise linear interpolation within the exposure bins
        defined by the relative risk lookup table.

        Parameters
        ----------
        builder
            Access point for utilizing framework interfaces during setup.

        Returns
        -------
            A callable that accepts a simulant index and returns
            interpolated relative risk values.
        """

        def generate_relative_risk(index: pd.Index) -> pd.Series:
            """Interpolate relative risk from exposure within RR bins."""
            rr_intervals = self.relative_risk_table(index)
            # NOTE: We are calling the cached exposure pipeline here for performance
            # purposes (as opposed to the f{self.causal_factor.name}.exposure pipeline itself).
            exposure = self.population_view.get(index, self.exposure_column_name)
            x1, x2 = (
                rr_intervals["left_exposure"].values,
                rr_intervals["right_exposure"].values,
            )
            y1, y2 = rr_intervals["left_rr"].values, rr_intervals["right_rr"].values
            m = (y2 - y1) / (x2 - x1)
            b = y1 - m * x1
            relative_risk = b + m * exposure
            return relative_risk

        return generate_relative_risk

    ##############
    # Validators #
    ##############

    def validate_rr_data(self, rr_data: pd.DataFrame) -> None:
        """Validate the relative risk data for non-log-linear effects.

        Verify that the ``parameter`` column contains numeric data and
        that values are monotonically increasing within each demographic
        group.

        Parameters
        ----------
        rr_data
            The relative risk data to validate.

        Raises
        ------
        ValueError
            If the relative risk data is empty, or if the ``parameter``
            column is not numeric or is not monotonically increasing
            within demographic groups.
        """
        if rr_data.empty:
            raise ValueError(
                f"The relative risk data for {self.causal_factor.name} affecting "
                f"{self.target.name} {self.target.measure} is empty. This can happen "
                "when the data contains no rows matching the affected entity and "
                "measure of this risk effect."
            )

        # check that rr_data has numeric parameter data
        parameter_data_is_numeric = rr_data["parameter"].dtype.kind in "biufc"
        if not parameter_data_is_numeric:
            raise ValueError(
                f"The parameter column in your {self.causal_factor.name} relative risk data must contain numeric data. Its dtype is {rr_data['parameter'].dtype} instead."
            )

        # and that these RR values are monotonically increasing within each demographic group
        # so that each simulant's exposure will assign them to either one bin or one RR value
        demographic_cols = self.get_demographic_columns(rr_data)

        def values_are_monotonically_increasing(df: pd.DataFrame) -> bool:
            """Check if parameter values are monotonically increasing."""
            return np.all(df["parameter"].values[1:] >= df["parameter"].values[:-1])

        group_is_increasing = rr_data.groupby(demographic_cols).apply(
            values_are_monotonically_increasing, include_groups=False
        )
        if not group_is_increasing.all():
            raise ValueError(
                "The parameter column in your relative risk data must be monotonically increasing to be used in NonLogLinearRiskEffect."
            )
