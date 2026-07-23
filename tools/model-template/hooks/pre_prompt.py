import json
import os
from datetime import datetime

import requests

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

    # Guard against PACKAGES <-> cookiecutter.json drift. Both directions are
    # bugs: a key in the JSON without a PACKAGES entry yields a blank pin in the
    # generated project; a key in PACKAGES without a JSON entry gets silently
    # written into the (temp) JSON as a phantom prompt, signaling that the
    # developer added a dep to PACKAGES but forgot to wire it into
    # cookiecutter.json + the templates.
    json_version_keys = {k for k in context if k.endswith("_version")}
    pkg_keys = set(PACKAGES)
    if json_version_keys != pkg_keys:
        parts = []
        if json_version_keys - pkg_keys:
            parts.append(
                f"in cookiecutter.json but not in PACKAGES: "
                f"{sorted(json_version_keys - pkg_keys)}"
            )
        if pkg_keys - json_version_keys:
            parts.append(
                f"in PACKAGES but not in cookiecutter.json: "
                f"{sorted(pkg_keys - json_version_keys)}"
            )
        raise ValueError("PACKAGES <-> cookiecutter.json drift: " + "; ".join(parts))

    context["current_year"] = str(datetime.now().year)

    for context_key, pypi_name in PACKAGES.items():
        context[context_key] = get_latest_version(pypi_name)

    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)


if __name__ == "__main__":
    main()
