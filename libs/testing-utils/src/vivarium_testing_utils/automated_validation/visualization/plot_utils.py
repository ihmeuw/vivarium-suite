# mypy: ignore-errors
from collections.abc import Collection
from typing import Any, Literal

import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from vivarium_testing_utils.automated_validation.comparison import Comparison
from vivarium_testing_utils.automated_validation.constants import DRAW_INDEX, SEED_INDEX
from vivarium_testing_utils.automated_validation.data_transformation import calculations


def plot_comparison(
    comparison: Comparison,
    type: str,
    condition: dict[str, Any] = {},
    stratifications: Collection[str] | Literal["all"] = "all",
    **kwargs: Any,
) -> Figure | list[Figure]:
    """Create a plot for the given comparison.

    Parameters
    ----------
    comparison
        The comparison object to plot.
    type
        Type of plot to create.
    condition
        Conditions to filter the data by, by default {}
    stratifications
        Stratifications to retain in the plotted dataset, by default "all"
    **kwargs
        Additional keyword arguments for specific plot types.

    Returns
    -------
        The generated figure or list of figures.
    """
    PLOT_TYPE_MAPPING = {
        "line": _line_plot,
        "bar": _bar_plot,
        "box": _box_plot,
        "heatmap": _heatmap,
    }
    if type not in PLOT_TYPE_MAPPING:
        raise NotImplementedError(
            f"Unsupported plot type: {type}. Supported types are: {list(PLOT_TYPE_MAPPING.keys())}"
        )
    title = comparison.measure.title

    # Add the scenario columns to the list of values to append to the title.
    for modifiers in (
        comparison.test_bundle.scenarios,
        comparison.reference_bundle.scenarios,
        condition,
    ):
        title = _append_condition_to_title(modifiers, title)

    combined_data = _get_combined_data(comparison, stratifications)
    combined_data = calculations.filter_data(combined_data, filter_cols=condition)

    default_kwargs = {
        "title": title,
        "combined_data": combined_data,
    }
    # error if "combined_data" is provided as a kwarg
    if "combined_data" in kwargs:
        raise ValueError(
            "The 'combined_data' argument is automatically generated from the comparison object. "
            "Please do not provide it as a keyword argument."
        )
    default_kwargs.update(kwargs)

    return PLOT_TYPE_MAPPING[type](**default_kwargs)


def plot_data(dataset: pd.DataFrame, type: str, kwargs):
    raise NotImplementedError


def save_plot(fig, name, format):
    raise NotImplementedError


def _line_plot(
    title: str,
    combined_data: pd.DataFrame,
    x_axis: str,
    subplots: bool = True,
) -> Figure | list[Figure]:
    """Create a line plot for the given data.

    Parameters
    ----------
    title
        Intended plot title.
    combined_data : pd.DataFrame
        Test and Reference data to plot.
    x_axis
        Column to use for the x-axis.
    subplots
        Whether to create subplots for each condition, by default True

    Returns
    -------
        The generated figure or list of figures.
    """

    LINEPLOT_KWARGS = {
        "marker": "o",
        "markers": True,
        "hue": "source",
        "y": "value",  # Assuming 'value' is the y-axis variable
        "errorbar": "pi",  # "Percent Interval", a nonparametric 95% CI
    }

    if subplots:
        return _rel_plot(
            title=title,
            combined_data=combined_data,
            x_axis=x_axis,
            plot_args=LINEPLOT_KWARGS,
        )
    else:
        unconditioned = _get_unconditioned_index_names(combined_data.index, x_axis)
        figures = []

        # Create individual figures for each condition
        for grouped_idx, grouped_df in combined_data.groupby(level=unconditioned):
            if not isinstance(grouped_idx, tuple):
                grouped_idx = (grouped_idx,)
            fig = plt.figure(figsize=(10, 6))
            ax = fig.add_subplot(111)

            condition_text = f"{' | '.join([f'{col} = {val}' for col, val in zip(unconditioned, grouped_idx)])}"
            condition_title = f"{title}\n {condition_text}"

            sns.lineplot(
                data=grouped_df.reset_index(),
                x=x_axis,
                ax=ax,
                **LINEPLOT_KWARGS,
            )

            ax.set_title(condition_title)
            ax.set_xlabel(x_axis)
            ax.set_ylabel("Proportion")
            ax.grid(alpha=0.5, color="gray")
            plt.tight_layout()

            figures.append(fig)

        return figures


def _rel_plot(
    title: str,
    combined_data: pd.DataFrame,
    x_axis: str = "age_group",
    plot_args: dict[str, Any] = {},
) -> Figure:
    """Create a stratified line plot using Seaborn's relplot.

    Parameters
    ----------
    title
        Main title for the plot
    combined_data
        Combined test and reference data
    x_axis
        Column to use for x-axis, by default "age_group"
    plot_args
        Additional arguments to pass to the plot, by default {}

    Returns
    -------
        The generated figure
    """
    ALLOWED_STRATIFICATIONS = 2
    unconditioned = _get_unconditioned_index_names(combined_data.index, x_axis)

    if len(unconditioned) > ALLOWED_STRATIFICATIONS:
        raise ValueError(
            "Maximum of {ALLOWED_STRATIFICATIONS} stratification levels supported with ."
            f"Please conditionalize {len(unconditioned) - ALLOWED_STRATIFICATIONS} of levels {unconditioned}."
        )

    # Set up relplot parameters based on stratifications
    relplot_kwargs = plot_args.copy()
    relplot_kwargs["facet_kws"] = {"sharex": False, "sharey": True}
    relplot_kwargs["kind"] = "line"

    if unconditioned:
        if len(unconditioned) == 2:
            first, second = unconditioned
            if (
                combined_data.index.get_level_values(first).nunique()
                > combined_data.index.get_level_values(second).nunique()
            ):
                relplot_kwargs["row"] = first
                relplot_kwargs["col"] = second
            else:
                relplot_kwargs["row"] = second
                relplot_kwargs["col"] = first
        else:
            relplot_kwargs["row"] = unconditioned[0]

    g = sns.relplot(data=combined_data.reset_index(), x=x_axis, **relplot_kwargs)

    g.set_axis_labels(x_axis, "Proportion")
    g.set_xticklabels(rotation=30)

    # Custom Legend
    g._legend.remove()
    g.figure.legend(loc="upper right")

    g.figure.suptitle(title, y=1.02, fontsize=16)
    g.map(plt.grid, alpha=0.5, color="gray")
    g.tight_layout()
    return g


def _bar_plot(
    title: str,
    test_data: pd.DataFrame,
    reference_data: pd.DataFrame,
    x_axis: str,
    stratifications: list[str],
):
    raise NotImplementedError


def _box_plot(
    title: str,
    test_data: pd.DataFrame,
    reference_data: pd.DataFrame,
    cat: str,
    stratifications: list[str],
):
    raise NotImplementedError


def _heatmap(
    title: str,
    test_data: pd.DataFrame,
    reference_data: pd.DataFrame,
    row: str,
    col: str,
):
    raise NotImplementedError


##################
# Helper Methods #
##################


def _get_unconditioned_index_names(
    index: pd.Index,
    x_axis: str,
) -> list[str]:
    """Get the columns that are not conditioned on."""
    all_index_names = index.names
    stat_cols = [DRAW_INDEX, SEED_INDEX]
    plotted_cols = ["source", x_axis]
    unconditioned = list(set(all_index_names) - set(stat_cols) - set(plotted_cols))
    return unconditioned


def _get_combined_data(
    comparison: Comparison, stratifications: Collection[str] | Literal["all"] = "all"
) -> pd.DataFrame:
    """Get the combined data from the test and reference datasets."""
    test_data, reference_data = comparison.align_datasets(stratifications)

    # Drop the scenario columns, which should already be filtered.
    test_data = calculations.filter_data(
        test_data, filter_cols=comparison.test_bundle.scenarios
    )
    reference_data = calculations.filter_data(
        reference_data, filter_cols=comparison.reference_bundle.scenarios
    )

    # Add input draw with placeholder if necessary
    if DRAW_INDEX in test_data.index.names and DRAW_INDEX not in reference_data.index.names:
        reference_data = reference_data.assign(input_draw=np.nan).set_index(
            [DRAW_INDEX], append=True
        )
    elif DRAW_INDEX not in test_data.index.names and DRAW_INDEX in reference_data.index.names:
        test_data = test_data.assign(input_draw=np.nan).set_index([DRAW_INDEX], append=True)
    test_data = test_data.reorder_levels(reference_data.index.names)

    combined_data = pd.concat(
        [test_data, reference_data],
        keys=[
            comparison.test_bundle.source.name.lower().capitalize(),
            comparison.reference_bundle.source.name.lower().capitalize(),
        ],
        names=["source"],
    )
    return combined_data


def _append_condition_to_title(condition_dict: dict[str, Any], title: str) -> str:
    """Append the condition dictionary to the title."""
    if condition_dict:
        title += f"\n{' | '.join([f'{k} = {v}' for k, v in condition_dict.items()])}"
    return title
