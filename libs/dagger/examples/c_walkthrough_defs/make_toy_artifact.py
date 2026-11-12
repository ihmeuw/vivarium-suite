"""Write a toy artifact that holds only the location the toy model runs in.

Usage: ``python make_toy_artifact.py <artifact>``. ``toy_simulation.py`` runs it as a
command step in the simulation env.
"""

import sys
from pathlib import Path

from vivarium.artifact import Artifact

if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.unlink(missing_ok=True)
    Artifact(path).write("metadata.locations", ["Toyland"])
