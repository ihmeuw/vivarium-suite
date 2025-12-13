from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from gbd_mapping import causes, covariates, risk_factors
from vivarium import Artifact
from vivarium.framework.artifact import EntityKey
from vivarium_inputs import interface
from vivarium_inputs.mapping_extension import alternative_risk_factors

from vivarium_testing_utils.automated_validation.constants import (
    DRAW_PREFIX,
    LOCATION_ARTIFACT_KEY,
    POPULATION_STRUCTURE_ARTIFACT_KEY,
    DataSource,
)
from vivarium_testing_utils.automated_validation.data_transformation import (
    calculations,
    utils,
)
from vivarium_testing_utils.automated_validation.data_transformation.data_schema import (
    SimOutputData,
)


class DataLoader:
    def __init__(self, sim_output_dir: Path, cache_size_mb: int = 1000):
        self._sim_output_dir = sim_output_dir
        self._cache_size_mb = cache_size_mb

        self._results_dir = self._sim_output_dir / "results"
        self._raw_data_cache: dict[
            DataSource, dict[str, pd.DataFrame | dict[str, str] | str]
        ] = {data_source: {} for data_source in DataSource}
        self._loader_mapping = {
            DataSource.SIM: self._load_from_sim,
            DataSource.GBD: self._load_from_gbd,
            DataSource.ARTIFACT: self._load_from_artifact,
        }
        self._artifact = self._load_artifact(self._sim_output_dir)

        # Initialize derived dataset person time total
        person_time_total = self._create_person_time_total_dataset()
        if person_time_total is not None:
            self._add_to_cache(
                data_key="person_time_total", data=person_time_total, source=DataSource.SIM
            )
        # TODO: MIC-6533 - Update when all locations are in one artifact in the future.
        self.location = self.get_data(LOCATION_ARTIFACT_KEY, DataSource.ARTIFACT)

    def _create_person_time_total_dataset(self) -> pd.DataFrame | None:
        """
        Create a derived dataset that aggregates total person time across all causes.

        This dataset can be used as a denominator for population-level measures like
        mortality rates.
        """
        all_outputs = self.get_sim_outputs()
        person_time_keys = [d for d in all_outputs if d.startswith("person_time_")]

        if not person_time_keys:
            return None  # No person time datasets to aggregate

        totals = []
        person_time_datasets = []
        for data_key in person_time_keys:
            data = self.get_data(data_key, DataSource.SIM)
            data = _convert_to_total_person_time(data)
            # Sum across all remaining stratifications
            total = data["value"].sum()
            totals.append(total)
            person_time_datasets.append(data)

        # get dataset with largest total
        largest_total_dataset = person_time_datasets[totals.index(max(totals))]

        return largest_total_dataset

    def get_sim_outputs(self) -> list[str]:
        """Get a list of the datasets in the given simulation output directory.
        Only return the filename, not the extension."""
        return list(
            set(str(f.stem) for f in self._results_dir.glob("*.parquet"))
            | set(self._raw_data_cache[DataSource.SIM].keys())
        )

    def get_artifact_keys(self) -> list[str]:
        return self._artifact.keys

    def get_data(self, data_key: str, source: DataSource) -> Any:
        """Return the data from the cache if it exists, otherwise load it from the source."""
        try:
            data = self._raw_data_cache[source][data_key]
            return data.copy() if isinstance(data, pd.DataFrame) else data
        except KeyError:
            if source == DataSource.CUSTOM:
                raise ValueError(
                    f"No custom data found for {data_key}."
                    "Please upload data using ValidationContext.upload_custom_data."
                )
            data = self._load_from_source(data_key, source)
            self._add_to_cache(data_key, source, data)
            return data

    def upload_custom_data(self, data_key: str, data: pd.DataFrame) -> None:
        self._add_to_cache(data_key, DataSource.CUSTOM, data)

    def cache_gbd_data(
        self,
        data_key: str,
        data: pd.DataFrame | dict[str, str] | str,
        overwrite: bool = False,
    ) -> None:
        """Upload or update a custom DataFrame or Series to the GBD context given by a data key."""
        if overwrite:
            if data_key in self._raw_data_cache[DataSource.GBD]:
                del self._raw_data_cache[DataSource.GBD][data_key]
        if data_key in self._raw_data_cache[DataSource.GBD] and not overwrite:
            existing = self._raw_data_cache[DataSource.GBD][data_key]
            if isinstance(existing, (dict, str)) or isinstance(data, (dict, str)):
                raise ValueError(
                    f"Existing GBD data for {data_key} is a type {type(existing)} and cannot be updated without the overwrite flag set to True."
                )
            else:
                if set(existing.index.names) != set(data.index.names):
                    raise ValueError(
                        f"Cannot update GBD data for {data_key} with different index names."
                        f" Existing index names: {existing.index.names}, new data index names: {data.index.names}"
                    )
                if not existing.index.equals(data.index):
                    # Check if the new data has non-overlapping indices with existing data
                    overlapping_indices = existing.index.intersection(data.index)
                    if len(overlapping_indices) > 0:
                        raise ValueError(
                            f"Cannot update GBD data for {data_key} with overlapping indices: {overlapping_indices.tolist()}"
                        )
                    # Append data to existing since indices don't overlap
                    data = pd.concat([existing, data])
                    del self._raw_data_cache[DataSource.GBD][data_key]

        if (
            isinstance(data, pd.DataFrame)
            and not data.columns.empty
            and data.columns.str.startswith(DRAW_PREFIX).all()
        ):
            data = calculations.clean_draw_columns(data)

        self._add_to_cache(data_key, DataSource.GBD, data)

    def _load_from_source(self, data_key: str, source: DataSource) -> Any:
        """Load the data from the given source via the loader mapping."""
        return self._loader_mapping[source](data_key)

    def _add_to_cache(
        self,
        data_key: str,
        source: DataSource,
        data: pd.DataFrame | pd.Series[float] | dict[str, str] | str,
    ) -> None:
        """Update the raw_data_cache with the given data."""
        if data_key in self._raw_data_cache.get(source, {}):
            raise ValueError(f"Data for {data_key} already exist in the cache.")
        if isinstance(data, pd.Series):
            data = data.to_frame(name="value")
        cache_data = data.copy() if isinstance(data, pd.DataFrame) else data
        self._raw_data_cache[source].update({data_key: cache_data})

    @utils.check_io(out=SimOutputData)
    def _load_from_sim(self, data_key: str) -> pd.DataFrame:
        """Load the data from the simulation output directory and set the non-value columns as indices."""
        sim_data = pd.read_parquet(self._results_dir / f"{data_key}.parquet")
        if "value" not in sim_data.columns:
            raise ValueError(f"{data_key}.parquet requires a column labeled 'value'.")
        multi_index_df = sim_data.set_index(sim_data.columns.drop("value").tolist())
        # ensure index levels are in order ["measure", "entity_type", "entity", "sub_entity"]
        # and then whatever else is in the index
        REQUIRED_INDEX_LEVELS = [
            "measure",
            "entity_type",
            "entity",
            "sub_entity",
        ]
        multi_index_df = multi_index_df.reorder_levels(
            [level for level in REQUIRED_INDEX_LEVELS]
            + [
                level
                for level in multi_index_df.index.names
                if level not in REQUIRED_INDEX_LEVELS
            ]
        )
        return multi_index_df

    @staticmethod
    def _load_artifact(results_dir: Path) -> Artifact:
        model_spec_path = results_dir / "model_specification.yaml"
        artifact_path = yaml.safe_load(model_spec_path.open("r"))["configuration"][
            "input_data"
        ]["artifact_path"]
        return Artifact(artifact_path)

    def _load_from_artifact(self, data_key: str) -> Any:
        """Load data directly from artifact, assuming correctly formatted data."""
        data = self._artifact.load(data_key)
        self._artifact.clear_cache()
        if (
            isinstance(data, pd.DataFrame)
            and not data.columns.empty
            and data.columns.str.startswith(DRAW_PREFIX).all()
        ):
            data = calculations.clean_draw_columns(data)
        return data

    def _load_from_gbd(self, data_key: str) -> Any:
        if "categories" in data_key:
            # Used for risk factor categories
            data = self._load_metadata(data_key, self.location)
        elif data_key == POPULATION_STRUCTURE_ARTIFACT_KEY:
            data = interface.get_population_structure(self.location)
        else:
            data = interface.load_standard_data(data_key, self.location)
        if (
            isinstance(data, pd.DataFrame)
            and not data.columns.empty
            and data.columns.str.startswith(DRAW_PREFIX).all()
        ):
            data = calculations.clean_draw_columns(data)
        return data

    def _get_raw_data_from_source(
        self, measure_keys: dict[str, str], source: DataSource
    ) -> dict[str, pd.DataFrame]:
        """Get the raw datasets from the given source."""
        return {
            dataset_name: self.get_data(data_key, source)
            for dataset_name, data_key in measure_keys.items()
        }

    def _load_metadata(self, key: str, location: str) -> Any:
        """Loads metadata for a given entity from GBD mapping. Generally will be in the
        form of dict[str, str]. Most commonly used for risk factor categories."""

        entity_key = EntityKey(key)
        type_map = {
            "cause": causes,
            "covariate": covariates,
            "risk_factor": risk_factors,
            "alternative_risk_factor": alternative_risk_factors,
        }
        entity = type_map[entity_key.type][entity_key.name]
        entity_metadata = entity[entity_key.measure]
        if hasattr(entity_metadata, "to_dict"):
            entity_metadata = entity_metadata.to_dict()
        return entity_metadata


#################
# Helper Methods#
#################


def _convert_to_total_person_time(data: pd.DataFrame) -> pd.DataFrame:
    old_index_names = data.index.names
    data = calculations.marginalize(data, ["entity_type", "entity", "sub_entity"])
    data["entity_type"] = "none"
    data["entity"] = "total"
    data["sub_entity"] = "total"
    # Reconstruct the index with the same column order as before
    data = data.reset_index().set_index(old_index_names)
    return data
