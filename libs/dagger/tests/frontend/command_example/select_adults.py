"""Copy the rows of a population CSV for the age groups of 15 and older to a new CSV.

Usage: ``python select_adults.py <population.csv> <adults.csv>``. It uses only the
standard library, so it runs in any environment that has Python.
"""

import csv
import sys


def select_adults(source: str, target: str) -> None:
    """Write the rows of ``source`` whose age group is not '0-14' to ``target``."""
    with open(source, newline="") as infile, open(target, "w", newline="") as outfile:
        reader = csv.DictReader(infile)
        writer = csv.DictWriter(outfile, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        writer.writerows(row for row in reader if row["age_group"] != "0-14")


if __name__ == "__main__":
    select_adults(sys.argv[1], sys.argv[2])
