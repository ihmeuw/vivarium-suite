"""Validate cookiecutter answers before the template is rendered.

Cookiecutter runs this hook after prompts but before any files are generated.
Raising SystemExit here aborts generation with a clear error, instead of
producing a broken project that only fails at pip install / pytest time.
"""

import re
import sys

PACKAGE_NAME = "{{cookiecutter.package_name}}"

# Python identifier rules: lowercase letters, digits, underscores; no leading
# digit. Enforced so package_name can be used as-is for the src/<pkg>/ directory,
# `import <pkg>` statements, and the pyproject.toml [project.scripts] entry
# (PEP 621 requires a valid python-entrypoint-reference here).
_VALID_PACKAGE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

if not _VALID_PACKAGE_NAME.match(PACKAGE_NAME):
    sys.stderr.write(
        f"ERROR: package_name '{PACKAGE_NAME}' is not a valid Python identifier.\n"
        f"Use lowercase letters, digits, and underscores only; no dashes; "
        f"must not start with a digit. Example: 'my_new_model'.\n"
    )
    sys.exit(1)
