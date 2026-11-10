"""Write one location's rows of the MNCNH IFA coverage table as a handoff file.

Usage: python ifa_extract.py <handoff root> [location_id] [location name]

This is the analyst's one-time step outside the pipeline: it reads the 795 MB all-location
table that ``load_ifa_coverage`` hard-codes and writes ``<root>/<location>/primary/ifa_coverage.csv``.
"""

import sys
from pathlib import Path

import pandas as pd

SOURCE = Path(
    "/snfs1/Project/simulation_science/mnch_grant/MNCNH portfolio/"
    "anc_iron_prop_st-gpr_results_aggregates_scaled2025-05-30.csv"
)


def main(root: Path, location_id: int, location: str) -> None:
    chunks = pd.read_csv(SOURCE, chunksize=20_000)
    rows = pd.concat(chunk[chunk["location_id"] == location_id] for chunk in chunks)
    out = root / location / "primary" / "ifa_coverage.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out, index=False)
    print(
        f"{len(rows)} rows, {rows.shape[1]} columns, years {sorted(rows.year_id.unique())} -> {out}"
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        Path(args[0]),
        int(args[1]) if len(args) > 1 else 214,
        args[2] if len(args) > 2 else "Nigeria",
    )
