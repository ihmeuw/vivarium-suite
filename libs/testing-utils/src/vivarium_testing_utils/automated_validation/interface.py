from __future__ import annotations

import base64
import html as html_module
import io
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Literal, Mapping

import matplotlib.pyplot as plt
import pandas as pd
import yaml
from IPython.display import HTML, display
from loguru import logger
from matplotlib.figure import Figure
from vivarium_inputs import utilities as vi

from vivarium_testing_utils.automated_validation.bundle import RatioMeasureDataBundle
from vivarium_testing_utils.automated_validation.comparison import (
    Comparison,
    FuzzyComparison,
    StratValue,
    TargetIntervalConfig,
)
from vivarium_testing_utils.automated_validation.constants import DAYS_PER_YEAR
from vivarium_testing_utils.automated_validation.data_loader import DataLoader, DataSource
from vivarium_testing_utils.automated_validation.data_transformation import report
from vivarium_testing_utils.automated_validation.data_transformation.calculations import (
    filter_data,
)
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    CategoricalRelativeRisk,
    Measure,
    MeasureMapper,
    RatioMeasure,
)
from vivarium_testing_utils.automated_validation.data_transformation.utils import (
    add_comparison_metadata_levels,
    drop_extra_columns,
    get_measure_index_names,
    set_gbd_index,
    set_validation_index,
)
from vivarium_testing_utils.automated_validation.results import VerificationResults
from vivarium_testing_utils.automated_validation.visualization import plot_utils
from vivarium_testing_utils.fuzzy_checker import TestResult


class ValidationContext:
    """Context for managing validation comparisons, results, and report generation."""

    def __init__(self, results_dir: str | Path, scenario_columns: Collection[str] = ()):
        self.results_dir = Path(results_dir)
        self.data_loader = DataLoader(self.results_dir)
        self.comparisons: dict[str, dict[str, Comparison]] = {}
        self.age_groups = self._get_age_groups()
        self.scenario_columns = scenario_columns
        self.location = self.data_loader.location
        self.measure_mapper = MeasureMapper()
        self.model_spec = self.data_loader.model_spec.configuration

    @property
    def verifications(self) -> VerificationResults:
        """Get the verification results dynamically from comparisons."""
        results = VerificationResults()
        for comparison_dict in self.comparisons.values():
            for comparison in comparison_dict.values():
                if comparison.verified is None:
                    continue

                source_key = f"{comparison.test_bundle.source.name.lower()}_{comparison.reference_bundle.source.name.lower()}"

                if comparison.verified:
                    results.passing[comparison.comparison_key][source_key] = comparison
                else:
                    results.failing[comparison.comparison_key][source_key] = comparison
        return results

    @property
    def stratification_metadata(
        self,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Get stratification metadata dynamically from comparisons.

        Returns a dict keyed by (measure_key, source_key) mapping to metadata
        about the available stratification dimensions for that comparison.
        """
        metadata: dict[tuple[str, str], dict[str, Any]] = {}
        for measure_key, source_dict in self.comparisons.items():
            for _source_key, comparison in source_dict.items():
                strat_meta = comparison.stratification_metadata
                if not strat_meta:
                    continue

                test_source = comparison.test_bundle.source.name.lower()
                ref_source = comparison.reference_bundle.source.name.lower()
                lookup_key = (measure_key, f"{test_source}_{ref_source}")
                metadata[lookup_key] = strat_meta
        return metadata

    def get_sim_outputs(self) -> list[str]:
        """Get a list of the datasets available in the given simulation output directory."""
        return sorted(self.data_loader.get_sim_outputs())

    def get_artifact_keys(self) -> list[str]:
        """Get a list of the artifact keys available to compare against."""
        return sorted(self.data_loader.get_artifact_keys())

    def get_raw_data(self, data_key: str, source: str) -> Any:
        """Return a copy of the data for manual inspection."""
        return self.data_loader.get_data(data_key, DataSource.from_str(source))

    def upload_custom_data(
        self, data_key: str, data: pd.DataFrame | pd.Series[float]
    ) -> None:
        """Upload a custom DataFrame or Series to the context given by a data key."""
        if isinstance(data, pd.Series):
            data = data.to_frame(name="value")
        self.data_loader.upload_custom_data(data_key, data)

    def add_comparison(
        self,
        measure_key: str,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
    ) -> None:
        """Add a comparison to the context given a measure key and data sources."""
        if measure_key.endswith(".relative_risk"):
            raise ValueError(
                f"For relative risk measures, use 'add_relative_risk_comparison' instead. "
                f"Got measure_key='{measure_key}'"
            )

        measure = self.measure_mapper.get_measure_from_key(
            measure_key, list(self.scenario_columns)
        )
        self._add_comparison_with_measure(
            measure,
            test_source,
            ref_source,
            test_scenarios,
            ref_scenarios,
        )

    def add_relative_risk_comparison(
        self,
        risk_factor: str,
        affected_entity: str,
        affected_measure: str,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
        risk_stratification_column: str | None = None,
        risk_state_mapping: dict[str, str] | None = None,
    ) -> None:
        """Add a relative risk comparison to the context.

        Parameters
        ----------
        risk_factor
            The risk factor name (e.g., 'child_stunting')
        affected_entity
            The entity affected by the risk factor (e.g., 'cause.diarrheal_diseases')
        affected_measure
            The measure to calculate (e.g., 'excess_mortality_rate', 'incidence_rate')
        risk_stratification_column
            The column to use for stratifying the risk factor in simulation data (e.g., 'risk_factor')
        test_source
            Source for test data ('sim', 'artifact', or 'custom')
        ref_source
            Source for reference data ('sim', 'artifact', or 'custom')
        test_scenarios
            Dictionary of scenario filters for test data
        ref_scenarios
            Dictionary of scenario filters for reference data
        """

        measure = CategoricalRelativeRisk(
            risk_factor,
            affected_entity,
            affected_measure,
            risk_stratification_column,
            risk_state_mapping,
        )
        self._add_comparison_with_measure(
            measure, test_source, ref_source, test_scenarios, ref_scenarios
        )

    def _add_comparison_with_measure(
        self,
        measure: Measure,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
    ) -> None:
        """Internal method to add a comparison with a pre-constructed measure."""

        test_source_enum = DataSource.from_str(test_source)
        ref_source_enum = DataSource.from_str(ref_source)

        # Check if the measure is a RatioMeasure for FuzzyComparison
        if not isinstance(measure, RatioMeasure):
            raise NotImplementedError(
                f"Measure {measure.measure_key} is not a RatioMeasure. Only RatioMeasures are currently supported for comparisons."
            )

        for source, scenarios in (
            (test_source_enum, test_scenarios),
            (ref_source_enum, ref_scenarios),
        ):
            if source == DataSource.SIM and set(scenarios.keys()) != set(
                self.scenario_columns
            ):
                raise ValueError(
                    f"Each simulation comparison subject must choose a specific scenario. "
                    f"You are missing scenarios for: {set(self.scenario_columns) - set(scenarios.keys())}."
                )

        test_data_bundle = RatioMeasureDataBundle(
            measure=measure,
            source=test_source_enum,
            data_loader=self.data_loader,
            age_group_df=self.age_groups,
            scenarios=test_scenarios,
        )
        ref_data_bundle = RatioMeasureDataBundle(
            measure=measure,
            source=ref_source_enum,
            data_loader=self.data_loader,
            age_group_df=self.age_groups,
            scenarios=ref_scenarios,
        )

        comparison = FuzzyComparison(
            test_bundle=test_data_bundle, reference_bundle=ref_data_bundle
        )
        self.comparisons.setdefault(measure.measure_key, {})[
            f"{test_source_enum.name.lower()}_{ref_source_enum.name.lower()}"
        ] = comparison

    def verify(
        self,
        comparison: str,
        test_source: str = "sim",
        ref_source: str = "artifact",
        stratifications: Collection[str] | Literal["all"] = "all",
    ) -> bool:
        """Verify a single comparison, log the result, and return True if successful, False otherwise.

        Parameters
        ----------
        comparison
            The key of the comparison to verify.
        test_source
            The source of the test data (e.g., 'sim', 'artifact', 'custom'). Default is "sim".
        ref_source
            The source of the reference data (e.g., 'sim', 'artifact', 'custom'). Default is "artifact".
        stratifications
            The stratifications to use for the comparison. Default is "all".

        Returns
        -------
            True if the comparison passes validation, False otherwise.
        """
        return self._verify(
            self.comparisons[comparison][f"{test_source}_{ref_source}"], stratifications
        )

    def _verify(
        self,
        comparison: Comparison,
        stratifications: Collection[str] | Literal["all"] = "all",
        verbose: bool = True,
    ) -> bool:
        if "step_size" in self.model_spec.time:
            step_size = self.model_spec.time.step_size / DAYS_PER_YEAR
        else:
            step_size = None
            logger.warning(
                "Step size is not defined in the model specification. This may result "
                "in inaccurate verification results."
            )

        comparison.verify(step_size, stratifications)
        if comparison.verified:
            logger.info(f"Comparison {comparison.comparison_key} passed!")
            return True
        else:
            logger.warning(f"Comparison {comparison.comparison_key} failed.")
            if verbose:
                if comparison.proportion_test_results["overall"].reject_null:
                    logger.warning(
                        f"Overall comparison for {comparison.comparison_key} failed."
                    )
                stratified = comparison.proportion_test_results.get("stratified", {})
                for group_dict in stratified.values():
                    for group in group_dict.values():
                        if group.reject_null:
                            logger.warning(
                                f"Group {group.name}_{group.name_additional} failed."
                            )
            return False

    def metadata(
        self, comparison_key: str, test_source: str, ref_source: str
    ) -> pd.DataFrame:
        """Get the metadata for a given comparison and specified sources.

        Parameters
        ----------
        comparison_key
            The key of the comparison for which to get the metadata
        test_source
            The source of the test data (e.g., 'sim', 'artifact', 'custom')
        ref_source
            The source of the reference data (e.g., 'sim', 'artifact', 'custom')

        Returns
        -------
            A DataFrame containing the metadata for the comparison in tabular format.
        """
        comparison_metadata = self.comparisons[comparison_key][
            f"{test_source}_{ref_source}"
        ].metadata
        directory_metadata = self._get_directory_metadata()

        data = pd.concat([comparison_metadata, directory_metadata])
        # Display draw values on multiple lines if necessary
        display_df = data.copy()
        display_df["Test Data"] = display_df["Test Data"].str.wrap(30, break_long_words=False)
        display_df["Reference Data"] = display_df["Reference Data"].str.wrap(
            30, break_long_words=False
        )

        display(HTML(display_df.to_html().replace("\\n", "<br>")))  # type: ignore[no-untyped-call]
        return data

    def _get_directory_metadata(self) -> pd.DataFrame:
        """Add model run metadata to the dictionary."""
        sim_run_time = self.results_dir.name
        sim_dt = datetime.strptime(sim_run_time, "%Y_%m_%d_%H_%M_%S").strftime(
            "%b %d %H:%M %Y"
        )
        artifact_run_time = self._get_artifact_creation_time()
        directory_metadata = pd.DataFrame(
            {
                "Property": ["Run Time"],
                "Test Data": [sim_dt],
                "Reference Data": [artifact_run_time],
            }
        )

        return directory_metadata.set_index("Property")

    def _get_artifact_creation_time(self) -> str:
        """Get the artifact creation time from the artifact file."""
        artifact_path = Path(
            yaml.safe_load((self.results_dir / "model_specification.yaml").open("r"))[
                "configuration"
            ]["input_data"]["artifact_path"]
        )
        os_time = os.path.getmtime(artifact_path)
        artifact_time = datetime.fromtimestamp(os_time).strftime("%b %d %H:%M %Y")

        return artifact_time

    def get_frame(
        self,
        comparison_key: str,
        test_source: str,
        ref_source: str,
        stratifications: Collection[str] | Literal["all"] = "all",
        num_rows: int | Literal["all"] = "all",
        sort_by: str = "",
        filters: Mapping[str, str | list[str]] | None = None,
        ascending: bool = False,
        aggregate_draws: bool = False,
    ) -> pd.DataFrame:
        """Get a DataFrame of the comparison data, with naive comparison of the test and reference.

        Parameters:
        -----------
        comparison_key
            The key of the comparison for which to get the data
        test_source
            The source of the test data (e.g., 'sim', 'artifact', 'custom')
        ref_source
            The source of the reference data (e.g., 'sim', 'artifact', 'custom')
        stratifications
            The stratifications to use for the comparison. If "all", no aggregation will happen and
            all existing stratifications will remain. If an empty list is passed, no stratifications
            will be retained.
        num_rows
            The number of rows to return. If "all", return all rows.
        filters
            A mapping of index levels to filter values. Only rows matching the filter will be included.
        sort_by
            The column to sort by. Default is "percent_error" for non-aggregated data, and no sorting for aggregated data.
        ascending
            Whether to sort in ascending order. Default is False.
        aggregate_draws
            If True, aggregate over draws to show means and 95% uncertainty intervals.

        Returns:
        --------
            A DataFrame of the comparison data.
        """
        if not aggregate_draws and not sort_by:
            sort_by = "percent_error"

        if (isinstance(num_rows, int) and num_rows > 0) or num_rows == "all":
            data = self.comparisons[comparison_key][f"{test_source}_{ref_source}"].get_frame(
                stratifications, num_rows, sort_by, ascending, aggregate_draws
            )
            data = self.format_ui_data_index(data, comparison_key)
            return (
                filter_data(data, filters, drop_singles=False)
                if filters is not None
                else data
            )
        else:
            raise ValueError("num_rows must be a positive integer or literal 'all'")

    def plot_comparison(
        self,
        comparison_key: str,
        test_source: str,
        ref_source: str,
        type: str,
        condition: dict[str, Any] = {},
        stratifications: Collection[str] | Literal["all"] = "all",
        **kwargs: Any,
    ) -> Figure | list[Figure]:
        """Create a plot for the given comparison.

        Parameters
        ----------
        comparison_key
            The comparison object to plot.
        test_source
            The source of the test data (e.g., 'sim', 'artifact', 'custom')
        ref_source
            The source of the reference data (e.g., 'sim', 'artifact', 'custom')
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
        return plot_utils.plot_comparison(
            self.comparisons[comparison_key][f"{test_source}_{ref_source}"],
            type,
            condition,
            stratifications,
            **kwargs,
        )

    def set_target_interval(
        self,
        comparison_key: str,
        test_source: str,
        ref_source: str,
        stratifications: dict[str, StratValue],
        relative_error: float,
    ) -> None:
        """Set a target interval configuration for a specific comparison.

        This configures a relative error to be applied to the target proportion
        for groups matching the stratification filter during verification.

        Parameters
        ----------
        comparison_key
            The key of the comparison to configure.
        test_source
            The source of the test data (e.g., 'sim', 'artifact', 'custom').
        ref_source
            The source of the reference data (e.g., 'sim', 'artifact', 'custom').
        stratifications
            A mapping of stratification names to filter values.
            - "all": match groups where this stratification is NOT present
            - "specific": match groups where this stratification IS present
            - A specific value: match only where that stratification has this value
        relative_error
            The relative error to apply, creating an interval of
            (target * (1 - relative_error), target * (1 + relative_error)).
        """
        comparison = self.comparisons[comparison_key][f"{test_source}_{ref_source}"]
        if not isinstance(comparison, FuzzyComparison):
            raise TypeError(
                f"target_interval_configuration is only supported for FuzzyComparison, "
                f"got {type(comparison).__name__}"
            )
        comparison.target_interval_configuration = TargetIntervalConfig(
            stratifications=stratifications, relative_error=relative_error
        )

    def generate_comparisons(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def verify_all(self) -> bool:
        """Verify all comparisons in the context and capture results.

        Returns
        -------
            True if all comparisons pass validation, False otherwise.

        """
        for comparison_dict in self.comparisons.values():
            for comparison in comparison_dict.values():
                self._verify(comparison, verbose=False)

        # Return True if no failing results (property dynamically computes results)
        return not any(self.verifications.failing.values())

    def plot_all(self, type: str, **kwargs: Any) -> dict[tuple[str, str, str], list[Figure]]:
        """Plot all comparisons and return dict of (measure_key, test_source, ref_source) -> list[Figure]."""
        figures_dict: dict[tuple[str, str, str], list[Figure]] = {}
        for comparison_dict in self.comparisons.values():
            for comparison in comparison_dict.values():
                test_source = comparison.test_bundle.source.name.lower()
                ref_source = comparison.reference_bundle.source.name.lower()
                fig = self.plot_comparison(
                    comparison.comparison_key,
                    test_source,
                    ref_source,
                    type,
                    **kwargs,
                )
                figs = fig if isinstance(fig, list) else [fig]
                figures_dict[(comparison.comparison_key, test_source, ref_source)] = figs
        return figures_dict

    def _figures_to_base64_dict(
        self, figures_dict: dict[tuple[str, str, str], list[Figure]]
    ) -> dict[tuple[str, str, str], list[str]]:
        """Convert a dict of (measure_key, test_source, ref_source) -> list[Figure] to base64 images."""
        plot_images: dict[tuple[str, str, str], list[str]] = {}
        for key, figs in figures_dict.items():
            images: list[str] = []
            for figure in figs:
                buf = io.BytesIO()
                figure.savefig(buf, format="png", bbox_inches="tight")
                buf.seek(0)
                img_b64 = base64.b64encode(buf.read()).decode("utf-8")
                images.append(img_b64)
                buf.close()
                # Handle both matplotlib Figure and seaborn FacetGrid
                underlying_fig = getattr(figure, "fig", figure)
                plt.close(underlying_fig)
            plot_images[key] = images
        return plot_images

    def generate_results(
        self,
        output_path: str | Path | None = None,
        plot_type: str = "line",
        display_in_notebook: bool = True,
        **kwargs: Any,
    ) -> None:
        """Generate an HTML report of validation results.

        This method runs verification for all comparisons, generates plots,
        and creates an interactive HTML report with tabbed navigation.

        Parameters
        ----------
        output_path
            Optional path to save the HTML report. If None, returns HTML string only.
        plot_type
            Type of plots to generate for comparisons (default: "line").
            Options: "line", "bar", "box", "heatmap"
        display_in_notebook
            If True (default), automatically displays the report in Jupyter notebooks.
            Set to False if you only want the HTML string returned without display.
        kwargs
            Additional keyword arguments to pass to the plotting function.

        Examples
        --------
        >>> # Auto-display in Jupyter (default behavior)
        >>> context = ValidationContext(results_dir="path/to/results")
        >>> context.add_comparison("cause.diarrhea.incidence_rate", "sim", "artifact")
        >>> context.generate_results()  # Automatically displays!

        >>> # Save to file and display
        >>> context.generate_results(output_path="report.html")

        """
        # Generate all plots as Figures using plot_all
        figures_dict = self.plot_all(plot_type, **kwargs)
        plot_images = self._figures_to_base64_dict(figures_dict)
        html_content = self._generate_report(plot_images=plot_images)

        if output_path is not None:
            saved_path = report.save_html_report(html_content, Path(output_path))
            logger.info(f"Report saved to: {saved_path}")

        # Auto-display in Jupyter notebooks if requested
        if display_in_notebook:
            try:
                # Check if we're in a Jupyter environment
                get_ipython()  # type: ignore[name-defined]
                # Use iframe with srcdoc so JavaScript executes properly
                escaped = html_module.escape(html_content)
                iframe_html = (
                    f'<iframe srcdoc="{escaped}" '
                    f'style="width:100%;height:800px;border:1px solid #ccc;border-radius:8px;" '
                    f'sandbox="allow-scripts"></iframe>'
                )
                display(HTML(iframe_html))  # type: ignore
            except NameError:
                # Not in Jupyter, skip display
                logger.info("Not in a Jupyter environment, cannot display the report.")
                pass

    def _generate_report(
        self, plot_images: dict[tuple[str, str, str], list[str]] | None = None
    ) -> str:
        """Generate HTML report content from validation results.

        This is the core report generation logic that collects data from
        comparisons and renders the HTML template.

        Returns
        -------
            HTML string of the generated report
        """
        # Run verification on all comparisons to populate results
        self.verify_all()

        # Collect summary statistics from verifications
        passing_count = sum(len(sources) for sources in self.verifications.passing.values())
        failing_count = sum(len(sources) for sources in self.verifications.failing.values())
        total_count = passing_count + failing_count

        # Calculate pass rate
        pass_rate = (passing_count / total_count * 100) if total_count > 0 else 0.0

        # Build list of comparison details for extended summary
        comparisons_list: list[dict[str, Any]] = []
        comparisons_list.extend(
            self._compile_comparison_results(self.verifications.passing, True)
        )
        comparisons_list.extend(
            self._compile_comparison_results(self.verifications.failing, False)
        )

        # Prepare the report data
        report_data = {
            "summary": {
                "total": total_count,
                "passing": passing_count,
                "failing": failing_count,
                "pass_rate": round(pass_rate, 1),
            },
            "comparisons": comparisons_list,
            "plot_images": plot_images or {},
            "filtered_failing_results": self._gather_filtered_test_results(),
        }

        # Generate the HTML report using the template
        html_content = report.create_html_report(report_data)

        return html_content

    def _compile_comparison_results(
        self, comparison_dict: Mapping[str, Mapping[str, Comparison]], passed: bool
    ) -> list[dict[str, Any]]:
        """Compile comparison results for report generation."""

        results = []
        for measure_key, source_dict in comparison_dict.items():
            for source_key, comparison in source_dict.items():
                test_source = comparison.test_bundle.source.name.lower()
                ref_source = comparison.reference_bundle.source.name.lower()
                overall_metadata = comparison.proportion_test_results["overall"].to_dict()
                all_test_results = self._extract_all_test_results(comparison)
                strat_meta = self.stratification_metadata.get((measure_key, source_key), {})
                results.append(
                    {
                        "measure_key": measure_key,
                        "test_source": test_source,
                        "ref_source": ref_source,
                        "passed": passed,
                        "overall_testresult": overall_metadata,
                        "all_testresults": all_test_results,
                        "stratification_metadata": strat_meta,
                        "passing_count": sum(
                            1 for tr in all_test_results if not tr["reject_null"]
                        ),
                        "failing_count": sum(
                            1 for tr in all_test_results if tr["reject_null"]
                        ),
                    }
                )
        return results

    def _extract_all_test_results(self, comparison: Comparison) -> list[dict[str, Any]]:
        """Collect all TestResults (overall and stratified) as a flat list of dicts."""
        results = []
        overall = comparison.proportion_test_results.get("overall")
        if overall:
            results.append(overall)
        stratified = comparison.proportion_test_results.get("stratified", {})
        if isinstance(stratified, dict):
            for group in stratified.values():
                for test_result in group.values():
                    results.append(test_result)
        return [test_result.to_dict() for test_result in results]

    def _gather_filtered_test_results(self) -> list[dict[str, Any]]:
        """Collect and filter test results based on importance of failures (if any).

        For each failing comparison, starts from the overall result and drills
        down through the lattice of stratification levels:

        - If a node failed and has no failing descendants: display it
        - If a node failed and a single child contains all failing descendants:
          skip the parent and recurse into that child
        - If a node failed and failures span multiple children: display the node
        - If a node passed: recurse into each child

        Results are sorted by bayes_factor in descending order (highest first).

        Returns
        -------
            Sorted list of filtered failing TestResult dicts.
        """
        all_filtered: list[dict[str, Any]] = []

        for source_dict in self.verifications.failing.values():
            for comparison in source_dict.values():
                overall = comparison.proportion_test_results.get("overall")
                stratified = comparison.proportion_test_results.get("stratified", {})

                if not overall:
                    continue

                # Collect all TestResults for this comparison
                all_results: list[TestResult] = [overall]
                if isinstance(stratified, dict):
                    for group in stratified.values():
                        for test_result in group.values():
                            all_results.append(test_result)

                # Build lattice lookups once per comparison
                children_map, failing_desc_map = self._build_lattice(all_results)

                # Run the lattice drill-down starting from overall
                displayed: list[TestResult] = []
                self._process_lattice_node(overall, children_map, failing_desc_map, displayed)
                all_filtered.extend(r.to_dict() for r in displayed)

        # Sort by bayes_factor descending (highest first)
        all_filtered.sort(key=lambda result: result["bayes_factor"], reverse=True)
        return all_filtered

    @staticmethod
    def _build_lattice(
        all_results: list[TestResult],
    ) -> tuple[dict[int, list[TestResult]], dict[int, set[int]]]:
        """Build the lattice adjacency list and failing-descendant index.

        Returns
        -------
        children
            Maps each node's id to its direct children (one additional dimension,
            matching values on shared dimensions).
        failing_descendants
            Maps each node's id to the set of ids of all strict descendants
            that reject the null.
        """
        children: dict[int, list[TestResult]] = {id(r): [] for r in all_results}
        failing_descendants: dict[int, set[int]] = {id(r): set() for r in all_results}

        for parent in all_results:
            parent_info = parent.index_info or {}
            parent_dims = frozenset(parent_info.keys())

            for candidate in all_results:
                candidate_info = candidate.index_info or {}
                candidate_dims = frozenset(candidate_info.keys())

                # Skip if not strictly more dimensions
                if len(candidate_dims) <= len(parent_dims):
                    continue
                # Skip if parent dims aren't a subset
                if not parent_dims.issubset(candidate_dims):
                    continue
                # Skip if shared values don't match
                if not all(candidate_info.get(k) == v for k, v in parent_info.items()):
                    continue

                # It's a descendant — check if direct child (exactly +1 dim)
                if len(candidate_dims) == len(parent_dims) + 1:
                    children[id(parent)].append(candidate)

                # Track failing descendants
                if candidate.reject_null:
                    failing_descendants[id(parent)].add(id(candidate))

        return children, failing_descendants

    def _process_lattice_node(
        self,
        node: TestResult,
        children_map: dict[int, list[TestResult]],
        failing_desc_map: dict[int, set[int]],
        displayed: list[TestResult],
    ) -> None:
        """Process a single node in the lattice drill-down algorithm.

        This method recursively drills down through each node of a data lattice structure and
        determines which TestResults to display for a given Comparison. It evaluates the node based
        on the passing or failing status of the node and its descendants.

        Parameters
        ----------
        node
            The current TestResult node being evaluated.
        children_map
            Pre-built map from node id to direct children.
        failing_desc_map
            Pre-built map from node id to ids of all failing strict descendants.
        displayed
            Accumulator list of TestResults to display.
        """
        children = children_map[id(node)]
        failing_ids = failing_desc_map[id(node)]

        if node.reject_null:
            if not failing_ids:
                # No failing checks beneath: display this node, stop
                displayed.append(node)
                return

            # Check if a single child contains ALL failing descendants
            for child in children:
                child_coverage = failing_desc_map[id(child)]
                if child.reject_null:
                    child_coverage = child_coverage | {id(child)}

                if failing_ids.issubset(child_coverage):
                    # Single child contains all failures — recurse, don't display parent
                    self._process_lattice_node(
                        child, children_map, failing_desc_map, displayed
                    )
                    return

            # No single child contains all failures: display parent, don't recurse
            displayed.append(node)
        else:
            # Node passed: recurse into each child
            for child in children:
                self._process_lattice_node(child, children_map, failing_desc_map, displayed)

    # TODO MIC-6047 Let user pass in custom age groups
    def _get_age_groups(self) -> pd.DataFrame:
        """Get the age groups from the given DataFrame or from the artifact."""
        from vivarium.framework.artifact.artifact import ArtifactException

        try:
            age_groups: pd.DataFrame = self.data_loader.get_data(
                "population.age_bins", DataSource.ARTIFACT
            )
        # If we can't find the age groups in the artifact, get them directly from vivarium inputs
        except ArtifactException:
            from vivarium_inputs import get_age_bins

            age_groups = get_age_bins()

        # mypy wants this to do type narrowing
        if age_groups is None:
            raise ValueError(
                "No age groups found. Please provide a DataFrame or use the artifact."
            )
            # relabel index level age_group_name to age_group

        return age_groups.rename_axis(index={"age_group_name": "age_group"})

    def cache_gbd_data(
        self,
        data_key: str,
        data: pd.DataFrame | dict[str, str] | str,
        overwrite: bool = False,
    ) -> None:
        """Upload the output of a get_draws call to the context given by a data key."""
        formatted_data: pd.DataFrame | dict[str, str] | str
        if isinstance(data, pd.DataFrame):
            formatted_data = self._format_to_vivarium_inputs_conventions(data, data_key)
            formatted_data = set_validation_index(formatted_data)
        else:
            formatted_data = data
        self.data_loader.cache_gbd_data(data_key, formatted_data, overwrite=overwrite)

    def _format_to_vivarium_inputs_conventions(
        self, data: pd.DataFrame, data_key: str
    ) -> pd.DataFrame:
        """Format the output of a get_draws call to data schema conventions for the validation context."""
        if "relative_risk" in data_key:
            data = vi.get_affected_measure_column(data)
        data = drop_extra_columns(data, data_key)
        data = set_gbd_index(data, data_key=data_key)
        data = vi.scrub_gbd_conventions(data, self.location)
        data = vi.split_interval(data, interval_column="age", split_column_prefix="age")
        data = vi.split_interval(data, interval_column="year", split_column_prefix="year")
        formatted_data: pd.DataFrame = vi.sort_hierarchical_data(data)
        return formatted_data

    @staticmethod
    def format_ui_data_index(data: pd.DataFrame, comparison_key: str) -> pd.DataFrame:
        """Format and sort the data for UI display.

        Parameters
        ----------
        data
            The DataFrame to sort.
        comparison_key
            The comparison key for logging purposes.

        Returns
        -------
            The sorted DataFrame.
        """

        expected_order = get_measure_index_names(comparison_key, "vivarium")
        ordered_cols = [col for col in expected_order if col in data.index.names]
        extra_idx_cols = [col for col in data.index.names if col not in ordered_cols]
        sorted_index = ordered_cols + extra_idx_cols
        sorted = data.reorder_levels(sorted_index).sort_index()
        return add_comparison_metadata_levels(sorted, comparison_key)

    def add_new_measure(self, measure_key: str, measure_class: type[Measure]) -> None:
        """Add a new measure class to the context's measure mapper.

        Parameters
        ----------
        measure_key
            The measure key in format 'entity_type.entity.measure_key' or 'entity_type.measure_key'.
        measure_class
            The class implementing the measure.
        """

        self.measure_mapper.add_new_measure(measure_key, measure_class)
