"""Merge-prep helpers: branch-squash gating and CHANGELOG date correction.

The pure logic behind the ``merge-prep`` make target and the label-triggered
``merge-prep`` workflow, which squash a feature branch and correct its CHANGELOG
release date just before it enters the merge queue. Run as
``python -m vivarium.build_utils.merge_prep <subcommand>``.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Branches whose commit history is preserved (never squashed): they reach main via
# rebase-and-merge so each task stays its own commit. Everything else is squashed.
PROTECTED_BRANCH_PREFIXES: tuple[str, ...] = (
    "epic/",
    "release-candidate/",
    "release_candidate/",
)

# CHANGELOG release dates are Pacific, matching the release workflow's date check.
_RELEASE_TIMEZONE = ZoneInfo("America/Los_Angeles")

# First CHANGELOG line, e.g. ``**4.2.13 - 07/22/26**``: version, then the date, then ``**``.
_CHANGELOG_HEADING = re.compile(r"^(\*\*\d+\.\d+\.\d+ - ).*?(\*\*\s*)$")


def should_squash(branch: str) -> bool:
    """Return whether ``branch``'s commits should be squashed at merge-prep."""
    return not branch.startswith(PROTECTED_BRANCH_PREFIXES)


def today_pacific() -> str:
    """Return today's Pacific date in the CHANGELOG format (``MM/DD/YY``)."""
    return datetime.now(_RELEASE_TIMEZONE).strftime("%m/%d/%y")


def update_changelog_date(content: str, today: str) -> str:
    """Rewrite the date on a CHANGELOG's first line to ``today``, keeping the version.

    Return ``content`` unchanged when the first line is not a ``**X.Y.Z - <date>**``
    heading, so a non-release CHANGELOG edit is never corrupted.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return content
    lines[0] = _CHANGELOG_HEADING.sub(rf"\g<1>{today}\g<2>", lines[0])
    return "".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Subcommands:

    ``should-squash <branch>``
        Exit 0 if ``branch`` should be squashed, 1 if it is protected. Lets a shell
        branch on the decision without duplicating the prefix list.

    ``fix-changelog-dates <file>...``
        Rewrite each file's first-line release date to today (Pacific), in place.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-merge-prep")
    subparsers = parser.add_subparsers(dest="command", required=True)

    squash_parser = subparsers.add_parser("should-squash")
    squash_parser.add_argument("branch")

    dates_parser = subparsers.add_parser("fix-changelog-dates")
    dates_parser.add_argument("files", nargs="*")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "should-squash":
        return 0 if should_squash(args.branch) else 1

    today = today_pacific()
    for file in args.files:
        path = Path(file)
        original = path.read_text()
        updated = update_changelog_date(original, today)
        if updated != original:
            path.write_text(updated)
            print(f"Corrected release date to {today} in {file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
