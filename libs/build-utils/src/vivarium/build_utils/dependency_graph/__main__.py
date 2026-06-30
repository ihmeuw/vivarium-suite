"""Entry point for ``python -m vivarium.build_utils.dependency_graph``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
