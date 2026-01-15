from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from vivarium_profiling.tools.extraction import ExtractionConfig

"""Benchmark visualization utilities."""

# Set style for better plots
plt.style.use("default")
sns.set_palette("husl")


def _plot_runtime_bars(ax: plt.Axes, df: pd.DataFrame, time_col: str, colors: list) -> None:
    """Plot runtime bar chart.

    Parameters
    ----------
    ax
        Matplotlib axes to plot on.
    df
        DataFrame with model data (already sorted).
    time_col
        Column name for time metric.
    colors
        List of colors for bars.

    """
    bars = ax.bar(range(len(df)), df[f"{time_col}_median"], color=colors)
    ax.set_title("Runtime by Model", fontweight="bold")
    ax.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    ax.set_ylabel("Median Runtime (seconds)")
    ax.set_xticks(range(len(df)))
    ax.set_xticklabels(df["model"], rotation=45, ha="right")
    # Add value labels on bars
    for _, bar in enumerate(bars):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{height:.1f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def _plot_memory_bars(ax: plt.Axes, df: pd.DataFrame, mem_col: str, colors: list) -> None:
    """Plot memory bar chart.

    Parameters
    ----------
    ax
        Matplotlib axes to plot on.
    df
        DataFrame with model data (already sorted).
    mem_col
        Column name for memory metric.
    colors
        List of colors for bars.

    """
    bars = ax.bar(range(len(df)), df[f"{mem_col}_median"], color=colors)
    ax.set_title("Peak Memory Usage by Model", fontweight="bold")
    ax.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    ax.set_ylabel("Median Peak Memory (MB)")
    ax.set_xticks(range(len(df)))
    ax.set_xticklabels(df["model"], rotation=45, ha="right")
    # Add value labels on bars
    for _, bar in enumerate(bars):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 2,
            f"{height:.0f}MB",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def _plot_scatter(
    ax: plt.Axes, df: pd.DataFrame, time_col: str, mem_col: str, colors: list
) -> None:
    """Plot runtime vs memory scatter with error bars.

    Parameters
    ----------
    ax
        Matplotlib axes to plot on.
    df
        DataFrame with model data (already sorted).
    time_col
        Column name for time metric.
    mem_col
        Column name for memory metric.
    colors
        List of colors for points.

    """
    ax.set_xlabel("Mean Runtime (seconds)")
    ax.set_ylabel("Mean Peak Memory (MB)")
    ax.set_title("Mean Runtime vs Mean Peak Memory Usage", fontweight="bold")
    ax.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    for i, row in df.iterrows():
        ax.errorbar(
            row[f"{time_col}_mean"],
            row[f"{mem_col}_mean"],
            xerr=row[f"{time_col}_std"],
            yerr=row[f"{mem_col}_std"],
            fmt="o",
            color=colors[i],
            ecolor=colors[i],
            elinewidth=1.5,
            capsize=3,
            markersize=8,
            alpha=0.7,
            linestyle="none",
        )
        # Add model labels to scatter points
        ax.annotate(
            row["model"],
            (row[f"{time_col}_mean"], row[f"{mem_col}_mean"]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
            alpha=0.8,
            color="black",
        )


def _plot_scale_factor(
    ax: plt.Axes, df: pd.DataFrame, time_pdiff_col: str, colors: list
) -> None:
    """Plot runtime percent difference vs scale factor.

    Parameters
    ----------
    ax
        Matplotlib axes to plot on.
    df
        DataFrame with model data (already sorted, with base_model and scale_factor).
    time_pdiff_col
        Column name for time percent difference.
    colors
        List of colors for lines.

    """
    ax.set_title("Runtime % Difference vs Scale Factor", fontweight="bold")
    ax.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    ax.set_xlabel("Scale Factor")
    ax.set_ylabel("Median Runtime % Difference")

    # Filter to only models with valid scale factors
    valid_models = df[df["scale_factor"].notna()]
    base_models = valid_models["base_model"].unique()

    for base_model in base_models:
        if base_model == "baseline":
            continue  # Skip baseline as it's the origin point

        model_group = valid_models[valid_models["base_model"] == base_model].sort_values(
            "scale_factor"
        )

        # Get color for this model type
        first_idx = df[df["base_model"] == base_model].index[0]
        line_color = colors[first_idx]

        # Prepare data for line plot (include baseline point at (1.0, 0))
        scale_factors = [1.0] + model_group["scale_factor"].tolist()
        pdiffs = [0.0] + model_group[time_pdiff_col].tolist()
        sorted_indices = np.argsort(scale_factors)
        scale_factors = np.array(scale_factors)[sorted_indices]
        pdiffs = np.array(pdiffs)[sorted_indices]

        # Plot line connecting all points for this model type
        ax.plot(
            scale_factors,
            pdiffs,
            color=line_color,
            alpha=0.6,
            linewidth=2,
            marker="o",
            markersize=6,
        )

        # Add label at the end of the line with just the base model name
        last_scale_factor = model_group["scale_factor"].iloc[-1]
        last_pdiff = model_group[time_pdiff_col].iloc[-1]
        ax.annotate(
            base_model,
            (last_scale_factor, last_pdiff),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=9,
            alpha=0.9,
            color="black",
        )

    # Add baseline point at (1.0, 0)
    ax.scatter(1.0, 0.0, color="gray", s=100, alpha=0.7, marker="o")

    # Add dashed linear reference line: (1.0, 0), (2.0, 100), (4.0, 300)
    ref_x = [1.0, 16.0]
    ref_y = [0, 1500]
    ax.plot(ref_x, ref_y, "--", color="gray", alpha=0.8, linewidth=2)


def create_figures(
    df: pd.DataFrame,
    output_dir: Path,
    chart_title: str,
    time_col: str,
    mem_col: str | None,
    time_pdiff_col: str,
) -> None:
    """Create performance analysis figures.

    Parameters
    ----------
    df
        Summary DataFrame with aggregated statistics.
    output_dir
        Directory to save the figures.
    chart_title
        Title for the output file.
    time_col
        Column name for time metric.
    mem_col
        Column name for memory metric (optional).
    time_pdiff_col
        Column name for time percent difference.

    """
    df = df.copy()
    # Create figure with subplots
    if mem_col:
        _, axes = plt.subplots(2, 2, figsize=(16, 12))
    else:
        _, axes = plt.subplots(1, 2, figsize=(16, 12))

    df_sorted = group_models_by_type(df)
    colors = assign_grouped_colors(df_sorted)

    # 1. Runtime comparison
    axes1 = axes[0, 0] if mem_col else axes[0]
    _plot_runtime_bars(axes1, df_sorted, time_col, colors)

    # 2. Peak memory comparison (if applicable)
    if mem_col:
        _plot_memory_bars(axes[0, 1], df_sorted, mem_col, colors)
        # 3. Runtime vs Memory scatter plot
        _plot_scatter(axes[1, 0], df_sorted, time_col, mem_col, colors)

    # 4. Plot runtime percent differences vs scale factor
    axes4 = axes[1, 1] if mem_col else axes[1]
    _plot_scale_factor(axes4, df_sorted, time_pdiff_col, colors)

    # save out
    plt.tight_layout()
    plt.savefig(output_dir / f"{chart_title}.png", dpi=300, bbox_inches="tight")
    # plt.show()  # Commented out for headless execution
    print(f"Saved {chart_title}.png'")


def plot_bottleneck_fractions(
    df: pd.DataFrame, output_dir: Path, config: ExtractionConfig, metric: str = "median"
) -> None:
    """Plot bottleneck fractions vs scale factor.

    Parameters
    ----------
    df
        Summary DataFrame with bottleneck fraction columns.
    output_dir
        Directory to save the figures.
    config
        Extraction configuration defining which bottlenecks to plot.
    metric
        Metric to plot (e.g., 'median', 'mean'). Default 'median'.

    """
    df = df.copy()
    # Prepare grouping and scale factors
    df = group_models_by_type(df)
    colors = assign_grouped_colors(df)

    # Filter to only models with valid scale factors
    df = df[df["scale_factor"].notna()]
    base_models = df["base_model"].unique()

    # Get bottlenecks from config
    bottleneck_patterns = [
        p
        for p in config.patterns
        if p.extract_cumtime and p.cumtime_col == f"{p.name}_cumtime"
    ]

    for pattern in bottleneck_patterns:
        y_col = f"{pattern.name}_fraction_{metric}"

        fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
        ax.set_title(f"{pattern.name} fraction vs scale factor", fontweight="bold")
        ax.set_xlabel("Scale factor")
        ax.set_ylabel(f"{metric.capitalize()} fraction of run_simulation.run()")
        ax.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)

        for base_model in base_models:
            if base_model == "baseline":
                continue
            model_group = df[df["base_model"] == base_model].sort_values("scale_factor")

            # Get color for this model type
            first_idx = df[df["base_model"] == base_model].index[0]
            line_color = colors[first_idx]

            # Prepare data for line plot

            scale_factors = [1.0] + model_group["scale_factor"].tolist()
            vals = (
                df.loc[df["base_model"] == "baseline", y_col].tolist()
                + model_group[y_col].tolist()
            )
            sorted_indices = np.argsort(scale_factors)
            scale_factors = np.array(scale_factors)[sorted_indices]
            vals = np.array(vals)[sorted_indices]

            # Plot line connecting all points for this model type
            ax.plot(
                scale_factors,
                vals,
                color=line_color,
                alpha=0.6,
                linewidth=2,
                marker="o",
                markersize=6,
            )

            # Add label at the end of the line with just the base model name
            last_scale_factor = model_group["scale_factor"].iloc[-1]
            last_val = model_group[y_col].iloc[-1]
            ax.annotate(
                base_model,
                (last_scale_factor, last_val),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=9,
                alpha=0.9,
                color="black",
            )

        # Plot baseline point (scale_factor=1.0) if present
        baseline = df[df["base_model"] == "baseline"]
        if not baseline.empty and y_col in baseline.columns:
            ax.scatter(
                baseline["scale_factor"],
                baseline[y_col],
                color="gray",
                s=70,
                marker="o",
                label="baseline",
                zorder=3,
            )

        # Sensible y-limits (fractions should be in [0,1], but leave headroom)
        ymin = max(0.0, np.nanmin(df[y_col].values) - 0.05)
        ymax = min(1.1, np.nanmax(df[y_col].values) + 0.05)
        if np.isfinite(ymin) and np.isfinite(ymax) and ymin < ymax:
            ax.set_ylim(ymin, ymax)
        # if base_models:
        #     ax.legend(frameon=False, fontsize=8, ncols=2)

        out_path = output_dir / f"bottleneck_fraction_{pattern.name}.png"
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved {out_path.name}")


def group_models_by_type(df: pd.DataFrame) -> pd.DataFrame:
    """Group models by their base type, keeping baseline first.

    Parameters
    ----------
    df
        DataFrame with a 'model' column.

    Returns
    -------
        DataFrame with additional 'base_model' and 'scale_factor' columns,
        sorted by base model and scale factor.

    """
    df = df.copy()
    df["base_model"] = df["model"].str.replace(r"_\d*\.?\d*x.*", "", regex=True)
    df["scale_factor"] = df["model"].str.extract(r"(\d*\.?\d+)x")[0]
    df["scale_factor"] = pd.to_numeric(df["scale_factor"], errors="coerce")
    df.loc[df["model"] == "baseline", "scale_factor"] = 1.0
    df = df.sort_values(["base_model", "scale_factor"])
    df = pd.concat([df[df["base_model"] == "baseline"], df[df["base_model"] != "baseline"]])
    df = pd.concat([df[df["scale_factor"].notna()], df[df["scale_factor"].isna()]])
    return df.reset_index(drop=True)


def assign_grouped_colors(df: pd.DataFrame) -> list:
    """Assign colors to models, grouping similar types together.

    Parameters
    ----------
    df
        DataFrame with 'base_model' column (typically output from group_models_by_type).

    Returns
    -------
        List of colors corresponding to each row in the DataFrame.

    """
    colors = []
    unique_models = df.loc[df["base_model"] != "baseline", "base_model"].unique()
    model_colors = plt.cm.Set1(np.linspace(0, 1, len(unique_models)))
    color_mapping = dict(zip(unique_models, model_colors))
    for _, row in df.iterrows():
        if row["base_model"] == "baseline":
            colors.append("gray")
        else:
            colors.append(color_mapping[row["base_model"]])
    return colors
