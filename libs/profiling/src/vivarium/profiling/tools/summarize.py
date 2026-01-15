from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from loguru import logger

from vivarium_profiling.tools.extraction import DEFAULT_BOTTLENECKS, ExtractionConfig

"""Benchmark summarization and visualization utilities."""

BASELINE = "model_spec_baseline.yaml"  # Default baseline model spec name
BASE_SUMMARIZE_COLUMNS = ["rt_s", "mem_mb", "rt_non_run_s"]

# Use bottleneck names from extraction module
BOTTLENECKS = [pattern.name for pattern in DEFAULT_BOTTLENECKS]

# Set style for better plots
plt.style.use("default")
sns.set_palette("husl")


def summarize(
    raw: pd.DataFrame, output_dir: Path, config: ExtractionConfig | None = None
) -> pd.DataFrame:
    """Summarize benchmark results with statistics and percent differences.

    Parameters
    ----------
    raw
        Raw benchmark results DataFrame with columns: model_spec, run, rt_s, mem_mb,
        and metric columns from extraction config.
    output_dir
        Directory to save summary.csv.
    config
        Extraction configuration defining metrics to summarize. If None, uses default.

    Returns
    -------
        Summary DataFrame with aggregated statistics and percent differences.

    """
    if config is None:
        config = ExtractionConfig()

    # Only bottlenecks (patterns with default cumtime template) get fraction calculations
    bottleneck_patterns = [
        p
        for p in config.patterns
        if p.extract_cumtime and p.cumtime_col == f"{p.name}_cumtime"
    ]

    summary = raw.copy()
    summary["rt_non_run_s"] = summary["rt_s"] - summary["rt_run_s"]

    # Calculate bottleneck fractions of run() time
    for pattern in bottleneck_patterns:
        if pattern.cumtime_col in summary.columns:
            summary[f"{pattern.name}_fraction"] = (
                summary[pattern.cumtime_col] / summary["rt_run_s"]
            )

    agg_dict = {}

    metric_columns = BASE_SUMMARIZE_COLUMNS + config.metric_columns
    fraction_columns = [f"{p.name}_fraction" for p in bottleneck_patterns]

    for col in metric_columns + fraction_columns:
        if col in summary.columns:
            agg_dict[f"{col}_mean"] = (col, "mean")
            agg_dict[f"{col}_median"] = (col, "median")
            agg_dict[f"{col}_std"] = (col, "std")
            agg_dict[f"{col}_min"] = (col, "min")
            agg_dict[f"{col}_max"] = (col, "max")

    summary = summary.groupby("model_spec").agg(**agg_dict).reset_index()

    # Fill NaN values in std columns with 0 (occurs with single observations)
    std_cols = [col for col in summary.columns if col.endswith("_std")]
    summary[std_cols] = summary[std_cols].fillna(0.0)

    # Calculate percent differences from baseline (median values)
    baseline_mask = summary["model_spec"].str.endswith(BASELINE)
    median_cols = [col for col in summary.columns if col.endswith("_median")]

    for median_col in median_cols:
        baseline_value = summary.loc[baseline_mask, median_col].values[0]
        pdiff_col = median_col.replace("_median", "_pdiff")
        summary[pdiff_col] = (summary[median_col] - baseline_value) / baseline_value * 100

    # Move the baseline row to the top
    summary = pd.concat(
        [
            summary.loc[summary["model_spec"].str.endswith(BASELINE)],
            summary.loc[~summary["model_spec"].str.endswith(BASELINE)],
        ]
    ).reset_index(drop=True)

    # Add model col for readability
    value_cols = [col for col in summary.columns if col != "model_spec"]
    summary["model"] = (
        summary["model_spec"]
        .str.split("/")
        .str[-1]
        .str.replace(".yaml", "")
        .str.replace("model_spec_", "")
    )
    summary = summary[["model_spec", "model"] + value_cols]
    summary_path = output_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)
    print("Saved summary.csv")

    if summary.isna().any().any():
        logger.warning("Unexpected NaNs found in summary data.")

    return summary


def create_figures(
    df: pd.DataFrame,
    output_dir: Path,
    chart_title: str,
    time_col: str,
    mem_col: str | None,
    time_pdiff_col: str,
) -> None:
    df = df.copy()
    # Create figure with subplots
    if mem_col:
        _, axes = plt.subplots(2, 2, figsize=(16, 12))
        # fig.suptitle('Performance Analysis', fontsize=12, fontweight='bold')
    else:
        _, axes = plt.subplots(1, 2, figsize=(16, 12))
        # fig.suptitle('Bottleneck Analysis', fontsize=12, fontweight='bold')

    df_sorted = group_models_by_type(df)
    colors = assign_grouped_colors(df_sorted)

    # 1. Runtime comparison
    axes1 = axes[0, 0] if mem_col else axes[0]
    bars1 = axes1.bar(range(len(df_sorted)), df_sorted[f"{time_col}_median"], color=colors)
    axes1.set_title("Runtime by Model", fontweight="bold")
    axes1.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    # axes1.set_xlabel('Model')
    axes1.set_ylabel("Median Runtime (seconds)")
    axes1.set_xticks(range(len(df_sorted)))
    axes1.set_xticklabels(df_sorted["model"], rotation=45, ha="right")
    # Add value labels on bars
    for _, bar in enumerate(bars1):
        height = bar.get_height()
        axes1.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{height:.1f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    # 2. Peak memory comparison
    if mem_col:
        bars2 = axes[0, 1].bar(
            range(len(df_sorted)), df_sorted[f"{mem_col}_median"], color=colors
        )
        axes[0, 1].set_title("Peak Memory Usage by Model", fontweight="bold")
        axes[0, 1].grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
        # axes[0,1].set_xlabel('Model')
        axes[0, 1].set_ylabel("Median Peak Memory (MB)")
        axes[0, 1].set_xticks(range(len(df_sorted)))
        axes[0, 1].set_xticklabels(df_sorted["model"], rotation=45, ha="right")
        # Add value labels on bars
        for _, bar in enumerate(bars2):
            height = bar.get_height()
            axes[0, 1].text(
                bar.get_x() + bar.get_width() / 2.0,
                height + 2,
                f"{height:.0f}MB",
                ha="center",
                va="bottom",
                fontsize=8,
            )

        # 3. Runtime vs Memory scatter plot with error bars - use grouped colors
        # Add error bars for standard deviations
        axes[1, 0].set_xlabel("Mean Runtime (seconds)")
        axes[1, 0].set_ylabel("Mean Peak Memory (MB)")
        axes[1, 0].set_title("Mean Runtime vs Mean Peak Memory Usage", fontweight="bold")
        axes[1, 0].grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
        for i, row in df_sorted.iterrows():
            axes[1, 0].errorbar(
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
            axes[1, 0].annotate(
                row["model"],
                (row[f"{time_col}_mean"], row[f"{mem_col}_mean"]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=8,
                alpha=0.8,
                color="black",
            )

    # 4. Plot runtime percent differences vs scale factor
    axes4 = axes[1, 1] if mem_col else axes[1]
    axes4.set_title("Runtime % Difference vs Scale Factor", fontweight="bold")
    axes4.grid(True, color="lightgray", alpha=0.7, linestyle="-", linewidth=0.5)
    axes4.set_xlabel("Scale Factor")
    axes4.set_ylabel("Median Runtime % Difference")

    # Filter to only models with valid scale factors
    valid_models = df_sorted[df_sorted["scale_factor"].notna()]
    base_models = valid_models["base_model"].unique()

    for base_model in base_models:
        if base_model == "baseline":
            continue  # Skip baseline as it's the origin point

        model_group = valid_models[valid_models["base_model"] == base_model].sort_values(
            "scale_factor"
        )

        # Get color for this model type
        first_idx = df_sorted[df_sorted["base_model"] == base_model].index[0]
        line_color = colors[first_idx]

        # Prepare data for line plot (include baseline point at (1.0, 0))
        scale_factors = [1.0] + model_group["scale_factor"].tolist()
        pdiffs = [0.0] + model_group[time_pdiff_col].tolist()
        sorted_indices = np.argsort(scale_factors)
        scale_factors = np.array(scale_factors)[sorted_indices]
        pdiffs = np.array(pdiffs)[sorted_indices]

        # Plot line connecting all points for this model type
        axes4.plot(
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
        axes4.annotate(
            base_model,
            (last_scale_factor, last_pdiff),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=9,
            alpha=0.9,
            color="black",
        )

    # Add baseline point at (1.0, 0)
    axes4.scatter(1.0, 0.0, color="gray", s=100, alpha=0.7, marker="o")

    # Add dashed linear reference line: (1.0, 0), (2.0, 100), (4.0, 300)
    ref_x = [1.0, 16.0]
    ref_y = [0, 1500]
    axes4.plot(ref_x, ref_y, "--", color="gray", alpha=0.8, linewidth=2)

    # save out
    plt.tight_layout()
    plt.savefig(output_dir / f"{chart_title}.png", dpi=300, bbox_inches="tight")
    # plt.show()  # Commented out for headless execution
    print(f"Saved {chart_title}.png'")


def plot_bottleneck_fractions(df: pd.DataFrame, output_dir: Path, metric: str) -> None:

    df = df.copy()
    # Prepare grouping and scale factors
    df = group_models_by_type(df)
    colors = assign_grouped_colors(df)

    # Filter to only models with valid scale factors
    df = df[df["scale_factor"].notna()]
    base_models = df["base_model"].unique()

    for bottleneck in BOTTLENECKS:
        y_col = f"{bottleneck}_fraction_{metric}"

        fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
        ax.set_title(f"{bottleneck} fraction vs scale factor", fontweight="bold")
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

        out_path = output_dir / f"bottleneck_fraction_{bottleneck}.png"
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved {out_path.name}")


def group_models_by_type(df: pd.DataFrame) -> pd.DataFrame:
    """Group models by their base type, keeping baseline first."""
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
    """Assign colors to models, grouping similar types together."""
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


def run_summarize_analysis(
    benchmark_results_filepath: Path, config: ExtractionConfig | None = None
) -> None:
    """Main function to run full summarize analysis pipeline.

    Parameters
    ----------
    benchmark_results_filepath
        Path to benchmark_results.csv file.
    config
        Extraction configuration. If None, uses default.

    """
    if config is None:
        config = ExtractionConfig()

    output_dir = benchmark_results_filepath.parent
    print(f"\nProcessing benchmark results from {benchmark_results_filepath}")
    print(f"Summarizing results to {output_dir}\n")

    raw = pd.read_csv(benchmark_results_filepath)
    if raw.isna().any().any():
        raise ValueError("NaNs found in raw data.")

    summary = summarize(raw, output_dir, config)

    create_figures(
        summary, output_dir, "performance_analysis", "rt_s", "mem_mb", "rt_s_pdiff"
    )

    create_figures(
        summary,
        output_dir,
        "runtime_analysis_setup",
        "rt_setup_s",
        None,
        "rt_setup_s_pdiff",
    )
    create_figures(
        summary,
        output_dir,
        "runtime_analysis_initialize_simulants",
        "rt_initialize_simulants_s",
        None,
        "rt_initialize_simulants_s_pdiff",
    )
    create_figures(
        summary, output_dir, "runtime_analysis_run", "rt_run_s", None, "rt_run_s_pdiff"
    )
    create_figures(
        summary,
        output_dir,
        "runtime_analysis_finalize",
        "rt_finalize_s",
        None,
        "rt_finalize_s_pdiff",
    )
    create_figures(
        summary,
        output_dir,
        "runtime_analysis_report",
        "rt_report_s",
        None,
        "rt_report_s_pdiff",
    )
    create_figures(
        summary,
        output_dir,
        "runtime_analysis_non_run",
        "rt_non_run_s",
        None,
        "rt_non_run_s_pdiff",
    )

    create_figures(
        summary,
        output_dir,
        "bottleneck_runtime_analysis_gather_results",
        "gather_results_cumtime",
        None,
        "gather_results_cumtime_pdiff",
    )
    create_figures(
        summary,
        output_dir,
        "bottleneck_runtime_analysis_pipeline_call",
        "pipeline_call_cumtime",
        None,
        "pipeline_call_cumtime_pdiff",
    )
    create_figures(
        summary,
        output_dir,
        "bottleneck_runtime_analysis_population_get",
        "population_get_cumtime",
        None,
        "population_get_cumtime_pdiff",
    )

    plot_bottleneck_fractions(summary, output_dir, "median")

    print("\n*** FINISHED ***")
