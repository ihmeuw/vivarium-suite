"""Sync a lib README's supported Python versions from its ``python_versions.json``.

Shared across the vivarium-suite monorepo so no lib copies its own updater (MIC-5670).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

# Capture the invariant prefix in group 1 and match only the version payload, so
# surrounding RST markup (e.g. ``**bold**``) is preserved. The trailing group
# repeats with ``*`` (not ``+``) so a single-version list still matches.
_ENUMERATED = re.compile(
    r"(Supported Python versions:[ \t]*)\d+\.\d+(?:[ \t]*,[ \t]*\d+\.\d+)*"
)

DEFAULT_README = "README.rst"
DEFAULT_VERSIONS_FILE = "python_versions.json"


def _version_key(version: str) -> tuple[int, ...]:
    """Return a numeric sort key so ``3.9`` orders before ``3.10``."""
    return tuple(int(part) for part in version.split("."))


def load_versions(path: Path) -> list[str]:
    """Load and numerically sort the supported versions from a python_versions.json.

    Parameters
    ----------
    path
        Path to the ``python_versions.json`` file.

    Returns
    -------
        The supported ``"X.Y"`` version strings, ascending.

    Raises
    ------
    ValueError
        If the file does not contain a non-empty JSON list.
    """
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON list of 'X.Y' strings")
    return sorted((str(version) for version in data), key=_version_key)


def update_readme_text(text: str, versions: list[str]) -> tuple[str, int]:
    """Rewrite the "Supported Python versions" line in README text to match ``versions``.

    Replaces only the enumerated version payload, so surrounding RST markup is
    preserved. Idempotent. Assumes ``X.Y`` (major.minor) versions.

    Parameters
    ----------
    text
        The README contents.
    versions
        Supported ``"X.Y"`` version strings, in any order.

    Returns
    -------
        The updated text and the number of substitutions made.
    """
    enumerated = ", ".join(sorted(versions, key=_version_key))
    return _ENUMERATED.subn(lambda m: f"{m.group(1)}{enumerated}", text)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="update-readme",
        description="Sync a README's supported-Python declarations from python_versions.json.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Package directory holding the README and python_versions.json (default: '.').",
    )
    parser.add_argument(
        "--readme",
        help=f"Override the README path (default: <root>/{DEFAULT_README}).",
    )
    parser.add_argument(
        "--versions-file",
        help=f"Override the versions file path (default: <root>/{DEFAULT_VERSIONS_FILE}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero with a diff if the README is out of sync; write nothing.",
    )
    parser.add_argument(
        "--require-line",
        action="store_true",
        help="Treat a README with no supported-Python marker as an error, not a warning.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the README updater as a CLI and return a process exit code."""
    args = _parse_args(argv)
    root = Path(args.root)
    readme_path = Path(args.readme) if args.readme else root / DEFAULT_README
    versions_path = (
        Path(args.versions_file) if args.versions_file else root / DEFAULT_VERSIONS_FILE
    )

    versions = load_versions(versions_path)
    original = readme_path.read_text()
    updated, substitutions = update_readme_text(original, versions)

    if substitutions == 0:
        message = f"{readme_path}: no supported-Python marker found."
        if args.require_line:
            print(f"ERROR: {message}", file=sys.stderr)
            return 1
        print(f"WARNING: {message} Skipping.", file=sys.stderr)
        return 0

    if args.check:
        if updated != original:
            sys.stderr.writelines(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=f"{readme_path} (current)",
                    tofile=f"{readme_path} (expected)",
                )
            )
            print(
                f"ERROR: {readme_path} is out of sync with {versions_path}. "
                "Run 'make update-readme' and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0

    if updated != original:
        readme_path.write_text(updated)
        print(f"Updated {readme_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
