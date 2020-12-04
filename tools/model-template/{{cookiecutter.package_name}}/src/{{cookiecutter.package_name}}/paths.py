from pathlib import Path

import {{cookiecutter.package_name}}
from {{cookiecutter.package_name}}.constants import metadata

BASE_DIR = Path({{cookiecutter.package_name}}.__file__).resolve().parent

ARTIFACT_ROOT = Path(f"/share/costeffectiveness/artifacts/{metadata.PROJECT_NAME}/")
MODEL_SPEC_DIR = BASE_DIR / 'model_specifications'
RESULTS_ROOT = Path(f'/share/costeffectiveness/results/{metadata.PROJECT_NAME}/')
