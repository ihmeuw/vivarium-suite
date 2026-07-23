import json
import os
from datetime import datetime

import requests

# Map cookiecutter.json version key -> PyPI distribution name.
# Names use dash-form as they appear on PyPI (PEP 503 normalizes underscore
# variants, but keeping the canonical form is cleaner). vivarium_inputs is
# still a standalone repo (not yet migrated to the monorepo), so its PyPI
# name is unchanged.
PACKAGES = {
    "vivarium_engine_version": "vivarium-engine",
    "vivarium_public_health_version": "vivarium-public-health",
    "vivarium_cluster_tools_version": "vivarium-cluster-tools",
    "vivarium_inputs_version": "vivarium_inputs",
    "vivarium_gbd_mapping_version": "vivarium-gbd-mapping",
    "vivarium_build_utils_version": "vivarium-build-utils",
}


def get_latest_version(package_name):
    """Fetch the latest version of a package from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data["info"]["version"]
    raise ValueError(f"Could not fetch version for {package_name}")


def main():
    """Update cookiecutter context dynamically with current package versions."""
    context_file = os.path.join(os.getcwd(), "cookiecutter.json")
    with open(context_file, "r") as file:
        context = json.load(file)

    context["current_year"] = str(datetime.now().year)

    for context_key, pypi_name in PACKAGES.items():
        context[context_key] = get_latest_version(pypi_name)

    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)


if __name__ == "__main__":
    main()
