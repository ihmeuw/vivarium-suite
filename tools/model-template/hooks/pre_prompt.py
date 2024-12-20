import os
import json
from datetime import datetime
import requests

def get_latest_version(package_name):
    """Fetch the latest version of a package from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data["info"]["version"]
    else:
        raise ValueError(f"Could not fetch version for {package_name}")

def main():
    """Update cookiecutter context dynamically with current package versions."""
    # Fetch versions for the required packages
    packages = [
        "vivarium",
        "vivarium_public_health",
        "vivarium_cluster_tools",
        "vivarium_inputs",
        "gbd_mapping",
    ]
    versions = {package_name: get_latest_version(package_name) for package_name in packages}

    # Update the context dynamically
    context_file = os.path.join(os.getcwd(), "cookiecutter.json")
    with open(context_file, "r") as file:
        context = json.load(file)

    # Set default for current year if not provided
    context["current_year"] = str(datetime.now().year)

    # Inject the fetched versions into the context
    for key, version in versions.items():
        context[f"{key}_version"] = version

    # Write updated context back to cookiecutter.json
    with open(context_file, "w") as file:
        json.dump(context, file, indent=4)

if __name__ == "__main__":
    main()
