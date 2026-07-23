import json
import os
import re
from datetime import datetime

import requests

# Public PyPI packages: fetched from https://pypi.org/pypi/<name>/json.
PYPI_PACKAGES = [
    "vivarium-engine",
    "vivarium-public-health",
    "vivarium-cluster-tools",
    "vivarium-gbd-mapping",
    "vivarium-build-utils",
]

# IHME-Artifactory-only packages: fetched from the Artifactory pypi-shared
# simple index (pypi.org doesn't have the current releases for these).
ARTIFACTORY_PACKAGES = [
    "vivarium-inputs",
]

def context_key(pypi_name):
    """cookiecutter.json version-key for a PyPI package name."""
    return f"{pypi_name.replace('-', '_')}_version"


def get_latest_from_pypi(package_name):
    """Fetch the latest version of a package from public PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()["info"]["version"]
    raise ValueError(f"Could not fetch version for {package_name} at {url}")


def get_latest_from_artifactory(package_name):
    """Fetch the latest version of a package from IHME Artifactory's pypi-shared.

    Artifactory's simple index is HTML, so we parse wheel/sdist filenames
    (``<normalized_name>-<version>-*``) and return the max semver.
    """
    url = f"https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/simple/{package_name}/"
    response = requests.get(url)
    if response.status_code != 200:
        raise ValueError(f"Could not fetch version for {package_name} at {url}")
    normalized = package_name.replace("-", "_")
    versions = re.findall(rf"{re.escape(normalized)}-(\d+\.\d+\.\d+)", response.text)
    if not versions:
        raise ValueError(f"No versions found for {package_name} at {url}")
    return sorted(versions, key=lambda v: tuple(int(x) for x in v.split(".")))[-1]


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
    pkg_keys = {context_key(n) for n in PYPI_PACKAGES + ARTIFACTORY_PACKAGES}
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

    for name in PYPI_PACKAGES:
        context[context_key(name)] = get_latest_from_pypi(name)
    for name in ARTIFACTORY_PACKAGES:
        context[context_key(name)] = get_latest_from_artifactory(name)

    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)


if __name__ == "__main__":
    main()
