from pathlib import Path

import {{cookiecutter.package_name}}.globals as project_globals

ARTIFACT_ROOT = Path(f"/share/costeffectiveness/artifacts/{project_globals.PROJECT_NAME}/")
MODEL_SPEC_DIR = (Path(__file__).parent / 'model_specifications').resolve()
