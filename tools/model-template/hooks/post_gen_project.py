import re

from packaging.version import parse


def update_vbu_pin():
    """Tighten the vivarium-build-utils pin with an upper bound (<next major).

    ``pre_prompt`` seeds ``cookiecutter.vivarium_build_utils_version`` with the
    current PyPI latest. The template renders it into ``pyproject.toml`` as a
    bare ``>=X.Y.Z``. This hook rewrites that to ``>=X.Y.Z,<(X+1).0.0`` so the
    generated project won't silently pick up a new major of vbu.
    """
    vbu_version = "{{cookiecutter.vivarium_build_utils_version}}"
    vbu_next_major = parse(vbu_version).major + 1
    bound_version = f"vivarium-build-utils>={vbu_version},<{vbu_next_major}.0.0"

    pyproject_path = "pyproject.toml"
    with open(pyproject_path, "r") as f:
        content = f.read()

    pattern = r'"vivarium-build-utils>=.*?"'
    replacement = f'"{bound_version}"'
    updated_content = re.sub(pattern, replacement, content)

    with open(pyproject_path, "w") as f:
        f.write(updated_content)


if __name__ == "__main__":
    update_vbu_pin()
