import json
import os
from datetime import datetime

import requests

# PyPI distribution names of the versioned dependencies the template pins.
# The cookiecutter.json key for each is derived by rule: dash -> underscore,
# then suffix _version (e.g. "vivarium-engine" -> "vivarium_engine_version").
PACKAGES = [
    "vivarium-engine",
    "vivarium-public-health",
    "vivarium-cluster-tools",
    "vivarium-inputs",
    "vivarium-gbd-mapping",
    "vivarium-build-utils",
]


def context_key(pypi_name):
    """cookiecutter.json version-key for a PyPI package name."""
    return f"{pypi_name.replace('-', '_')}_version"


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
    pkg_keys = {context_key(name) for name in PACKAGES}
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

    for pypi_name in PACKAGES:
        context[context_key(pypi_name)] = get_latest_version(pypi_name)

    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)


if __name__ == "__main__":
    main()
