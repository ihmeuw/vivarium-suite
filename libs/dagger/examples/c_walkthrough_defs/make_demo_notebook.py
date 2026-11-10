"""Copy MNCNH's hemorrhage split notebook with a ``parameters`` cell that notebook_step can fill.

Usage: python make_demo_notebook.py <mncnh checkout> <destination .ipynb>

The model's notebook finds its artifacts through the model spec's ``artifact_path``. Without
a ``parameters`` cell, papermill injects the parameter at the top and cell 4 then overwrites
it, so the notebook silently reads the model's own artifacts (the MIC-7755 case). This copy
adds the cell and makes cell 4 use the injected path when one is given. That is the change
a modeler makes to adopt notebook_step.
"""

import json
import sys
from pathlib import Path

SOURCE = "tests/model_notebooks/artifact/hemorrhage_split_proportions.ipynb"
RESOLVE_CELL = 4

PARAMETERS = {
    "cell_type": "code",
    "execution_count": None,
    "metadata": {"tags": ["parameters"]},
    "outputs": [],
    "source": [
        "artifact = None  # path to one artifact HDF; None reads the model spec's artifact_path"
    ],
}

RESOLVE = """\
if artifact is not None:
    base_artifacts_dir = Path(artifact).parent
else:
    # Resolve the artifact directory from the model spec's artifact_path (the single
    # source of truth) instead of a hand-maintained model_dir string.
    base_artifacts_dir = Path(
        yaml.safe_load(paths.MODEL_SPEC_PATH.read_text())
        ['configuration']['input_data']['artifact_path']
    ).parent
base_artifacts_dir"""


def main(checkout: Path, destination: Path) -> None:
    notebook = json.loads((checkout / SOURCE).read_text())
    cells = notebook["cells"]
    if "base_artifacts_dir = Path(" not in "".join(cells[RESOLVE_CELL]["source"]):
        raise SystemExit(
            f"Cell {RESOLVE_CELL} of {SOURCE} no longer resolves the artifact dir."
        )
    cells[RESOLVE_CELL]["source"] = RESOLVE.splitlines(keepends=True)
    cells.insert(RESOLVE_CELL, PARAMETERS)
    for cell in cells:
        if cell["cell_type"] == "code":
            cell["outputs"], cell["execution_count"] = [], None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(notebook, indent=1) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
