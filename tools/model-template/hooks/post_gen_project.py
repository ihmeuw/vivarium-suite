import re
from packaging.version import parse

def update_vbu_pin():
    """Update the vivarium_build_utils pin with upper bound."""
    vbu_version = "{{cookiecutter.vivarium_build_utils_version}}"
    vbu_next_major = parse(vbu_version).major + 1
    bound_version = f"vivarium_build_utils>={vbu_version},<{vbu_next_major}.0.0"
    
    # Read setup.py
    setup_py_path = "setup.py"
    with open(setup_py_path, "r") as f:
        content = f.read()
    
    # Replace the simple version pin with the bound version
    pattern = r'"vivarium_build_utils>=.*?"'
    replacement = f'"{bound_version}"'
    updated_content = re.sub(pattern, replacement, content)
    
    with open(setup_py_path, "w") as f:
        f.write(updated_content)

if __name__ == "__main__":
    update_vbu_pin()
