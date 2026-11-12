"""Write the slice's three loaded keys into a fresh Nigeria artifact, as ``make_artifacts`` would.

Usage: ``python write_artifact.py <postpartum_fraction> <demography> <ifa_coverage> <artifact>``.
``mncnh_slice.py`` runs it as a command step in MNCNH's artifact env.
"""

import sys
from pathlib import Path

import pandas as pd
from vivarium.artifact import Artifact
from vivarium_gates_mncnh.constants import data_keys

LOCATION = "Nigeria"


def main(postpartum_fraction: str, demography: str, ifa_coverage: str, artifact: str) -> None:
    """Write the three parquet files and the location into a new artifact."""
    path = Path(artifact)
    path.unlink(missing_ok=True)
    hdf = Artifact(path)
    hdf.write(data_keys.METADATA_LOCATIONS, [LOCATION])
    hdf.write(data_keys.POPULATION.DEMOGRAPHY, pd.read_parquet(demography))
    hdf.write(
        data_keys.MATERNAL_HEMORRHAGE.POSTPARTUM_FRACTION,
        pd.read_parquet(postpartum_fraction),
    )
    hdf.write(data_keys.IFA_SUPPLEMENTATION.COVERAGE, pd.read_parquet(ifa_coverage))


if __name__ == "__main__":
    main(*sys.argv[1:])
