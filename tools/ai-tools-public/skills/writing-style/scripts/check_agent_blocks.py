#!/usr/bin/env python3
"""Report agent files whose prose line is missing or differs from the canonical copy.

The canonical line is in ``references/agent-block.md``. The script requires the line in
each agent named in ``REQUIRED_AGENTS`` and in each file argument. It also reports a
changed copy of the line in any other agent file of this plugin.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BLOCK_FILE = SKILL_DIR / "references" / "agent-block.md"
DEFAULT_AGENTS_DIR = SKILL_DIR.parent.parent / "agents"
PREFIX = "- Write prose for people"
REQUIRED_AGENTS = (
    "_review_design.md",
    "_review_documentation.md",
    "_review_dry.md",
    "_review_maintainability.md",
    "_review_tests.md",
    "_split_proposer.md",
)


def canonical_line(block_file: Path = BLOCK_FILE) -> str:
    """Return the line between the markers in the canonical block file."""
    match = re.search(
        r"<!-- agent-block:begin -->\n(.*?)\n<!-- agent-block:end -->",
        block_file.read_text(),
        re.DOTALL,
    )
    if match is None:
        raise ValueError(f"no agent-block markers in {block_file}")
    return match.group(1).strip()


def problems(path: Path, canonical: str, required: bool) -> list[str]:
    """Return the problems with the prose line in one agent file."""
    copies = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip().startswith(PREFIX)
    ]
    if not copies:
        return [f"{path}: missing the prose line"] if required else []
    return [
        f"{path}: prose line differs from {BLOCK_FILE.name}" for c in copies if c != canonical
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the check from the command line and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "files", nargs="*", type=Path, help="agent files that must have the line"
    )
    args = parser.parse_args(argv)
    required = [DEFAULT_AGENTS_DIR / name for name in REQUIRED_AGENTS] + args.files
    required_paths = {p.resolve() for p in required}
    others = [
        p
        for p in sorted(DEFAULT_AGENTS_DIR.glob("*.md"))
        if p.resolve() not in required_paths
    ]
    try:
        canonical = canonical_line()
        found = [p for f in required for p in problems(f, canonical, required=True)]
        found += [p for f in others for p in problems(f, canonical, required=False)]
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(f"check_agent_blocks: {error}", file=sys.stderr)
        return 2
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
