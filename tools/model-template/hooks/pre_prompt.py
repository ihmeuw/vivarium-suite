import json
import os
from datetime import datetime

import requests
from packaging.version import parse

# Map cookiecutter.json version key -> PyPI distribution name.
PACKAGES = {
    "vivarium_engine_version": "vivarium-engine",
    "vivarium_public_health_version": "vivarium-public-health",
    "vivarium_cluster_tools_version": "vivarium-cluster-tools",
    "vivarium_inputs_version": "vivarium-inputs",
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

    # Guard against PACKAGES <-> cookiecutter.json drift: any *_version key
    # declared in the static JSON must have a fetch entry, else the generated
    # project gets a blank version pin.
    unfetched = {k for k in context if k.endswith("_version")} - set(PACKAGES)
    if unfetched:
        raise ValueError(
            f"cookiecutter.json declares {sorted(unfetched)} but PACKAGES has "
            f"no fetch entry. Add them to PACKAGES in this file."
        )

    context["current_year"] = str(datetime.now().year)

    for context_key, pypi_name in PACKAGES.items():
        context[context_key] = get_latest_version(pypi_name)

    # Expose next-major of vivarium-build-utils so the generated pyproject.toml
    # can render a bounded pin (>=X.Y.Z,<(X+1).0.0) at template time. Keeps the
    # pin logic in one place and eliminates the need for a post-gen fixup.
    vbu = context["vivarium_build_utils_version"]
    context["vivarium_build_utils_next_major_version"] = str(parse(vbu).major + 1)

    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)


if __name__ == "__main__":
    main()
