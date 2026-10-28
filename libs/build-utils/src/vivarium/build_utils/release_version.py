"""Validate release versions: a final ``X.Y.Z`` or a beta ``X.Y.ZbN``.

Backs ``make validate-tag``, which the release workflows run before they create a
tag, and supplies the start tag for a final release's GitHub notes. Usage::

    python -m vivarium.build_utils.release_version validate-tag
    python -m vivarium.build_utils.release_version notes-start-tag --version 1.3.0

Both read ``TAG_PREFIX`` from the environment (empty for standalone repos,
``<dist>-`` for monorepo libs). ``validate-tag`` reads the tag from
``RELEASE_TAG``, falling back to ``GITHUB_REF_NAME``, and the CHANGELOG from
``./CHANGELOG.rst``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from packaging.version import Version

# Finds a release version in free text, such as a CHANGELOG heading. The grep
# patterns in base.mk, release.yml, and has_changelog_update.groovy must match it.
RELEASE_VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(b[0-9]+)?"

_STRICT_RELEASE_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(b[1-9][0-9]*)?"
)
_CHANGELOG_HEADING = re.compile(r"\*\*(\S+)")


def is_release_version(version: str) -> bool:
    """Return whether ``version`` is a final ``X.Y.Z`` or a beta ``X.Y.ZbN``.

    Parameters
    ----------
    version
        The version string, without a tag prefix or ``v``.

    Returns
    -------
        True for a version such as ``1.3.0`` or ``1.3.0b1``. False for leading
        zeros, ``b0``, and every other pre-release form, such as ``a1``, ``rc1``,
        or ``1.3.0-beta.1``.
    """
    return _STRICT_RELEASE_VERSION.fullmatch(version) is not None


def read_changelog_version(changelog: Path) -> str:
    """Return the version token of the first ``**...`` heading in a CHANGELOG.

    Parameters
    ----------
    changelog
        Path to a ``CHANGELOG.rst``.

    Returns
    -------
        The heading's first word, for example ``1.3.0b1`` from
        ``**1.3.0b1 - 11/03/26**``. It is not checked against the release format.

    Raises
    ------
    FileNotFoundError
        If the CHANGELOG does not exist.
    ValueError
        If the CHANGELOG has no line that starts with ``**``.
    """
    if not changelog.exists():
        raise FileNotFoundError(f"CHANGELOG file not found at {changelog}")
    for line in changelog.read_text().splitlines():
        match = _CHANGELOG_HEADING.match(line)
        if match:
            return match.group(1)
    raise ValueError(f"Could not find a changelog heading in format '**...' in {changelog}")


def is_one_step_bump(previous: Version, current: Version) -> bool:
    """Return whether ``current`` is one patch, minor, or major step after ``previous``.

    Parameters
    ----------
    previous
        The last final release.
    current
        The final version to check. For a beta, pass its final version.

    Returns
    -------
        True for ``X.Y.Z -> X.Y.(Z+1)``, ``X.(Y+1).0``, or ``(X+1).0.0``.
    """
    major, minor, patch = previous.release
    return current.release in (
        (major, minor, patch + 1),
        (major, minor + 1, 0),
        (major + 1, 0, 0),
    )


def check_bump(current: str, released: Iterable[str]) -> str | None:
    """Check that ``current`` follows the released versions by one allowed step.

    The allowed steps are:

    - final to final: one patch, minor, or major step from the last final.
    - final to beta: ``b1`` of a version one step from the last final.
    - beta to beta: ``bN`` to ``b(N+1)`` of the same version.
    - beta to final: the final of a version one step from the last final.

    Versions released before ``current`` decide the step, so a patch release of
    an older line still passes.

    Parameters
    ----------
    current
        The version being released, already checked with :func:`is_release_version`.
    released
        The versions already released for this package. ``current`` itself is
        ignored if present.

    Returns
    -------
        None if the step is allowed, otherwise an error message.
    """
    cur = Version(current)
    all_released = {Version(version) for version in released} - {cur}
    earlier = sorted(version for version in all_released if version < cur)
    finals = [version for version in earlier if not version.is_prerelease]
    target = Version(cur.base_version)

    if cur.pre is not None:
        if target in all_released:
            return f"{target} is already released, so it cannot get a new beta {cur}."
        previous = earlier[-1] if earlier else None
        if (
            previous is not None
            and previous.pre is not None
            and previous.base_version == cur.base_version
        ):
            if cur.pre[1] != previous.pre[1] + 1:
                return f"The beta after {previous} must be {target}b{previous.pre[1] + 1}, not {cur}."
            return None
        if cur.pre[1] != 1:
            return f"The first beta of {target} must be {target}b1, not {cur}."

    if finals and not is_one_step_bump(finals[-1], target):
        return (
            f"{target} is not one step from the last final release {finals[-1]}. "
            "Expected a patch (X.Y.Z -> X.Y.(Z+1)), minor (X.Y.Z -> X.(Y+1).0), or "
            "major (X.Y.Z -> (X+1).0.0) step."
        )
    return None


def notes_start_tag(current: str, released: Iterable[str], tag_prefix: str) -> str | None:
    """Return the tag a final release's GitHub notes should start from.

    GitHub starts generated notes at the previous release. After betas that is the
    last beta, so the notes would leave out the changes in the earlier betas.

    Parameters
    ----------
    current
        The version being released.
    released
        The versions already released for this package.
    tag_prefix
        The tag prefix, empty for standalone repos.

    Returns
    -------
        The tag of the last final release when ``current`` is a final that follows
        a beta, otherwise None so GitHub uses its default.
    """
    cur = Version(current)
    if cur.is_prerelease:
        return None
    earlier = sorted(Version(version) for version in released if Version(version) < cur)
    if not earlier or not earlier[-1].is_prerelease:
        return None
    finals = [version for version in earlier if not version.is_prerelease]
    return f"{tag_prefix}v{finals[-1]}" if finals else None


def released_versions(tag_prefix: str) -> list[str]:
    """Return the release versions of the git tags that start with ``<tag_prefix>v``.

    Parameters
    ----------
    tag_prefix
        The tag prefix, empty for standalone repos.

    Returns
    -------
        The versions, without prefix or ``v``. Tags that are not a release
        version are left out.
    """
    tags = subprocess.run(
        ["git", "tag", "--list", f"{tag_prefix}v*"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    versions = [tag.removeprefix(tag_prefix).removeprefix("v") for tag in tags]
    return [version for version in versions if is_release_version(version)]


def validate_tag(
    release_tag: str, tag_prefix: str, changelog: Path, released: Iterable[str]
) -> str:
    """Validate a release tag against the CHANGELOG and the released versions.

    Parameters
    ----------
    release_tag
        The tag to validate, for example ``vivarium-engine-v1.3.0b1``.
    tag_prefix
        The tag prefix, empty for standalone repos.
    changelog
        Path to the package's ``CHANGELOG.rst``.
    released
        The versions already released for this package.

    Returns
    -------
        The validated version.

    Raises
    ------
    ValueError
        If the tag or CHANGELOG version is not a release version, if they differ,
        or if the version is not an allowed step from the released versions.
    """
    version = release_tag.removeprefix(tag_prefix).removeprefix("v")
    if not is_release_version(version):
        raise ValueError(
            f"Tag '{release_tag}' must be {tag_prefix}vX.Y.Z or {tag_prefix}vX.Y.ZbN."
        )
    changelog_version = read_changelog_version(changelog)
    if not is_release_version(changelog_version):
        raise ValueError(
            f"Latest CHANGELOG version '{changelog_version}' must be X.Y.Z or X.Y.ZbN."
        )
    if version != changelog_version:
        raise ValueError(
            f"Version mismatch: tag {release_tag} has {version}, but the latest "
            f"CHANGELOG entry is {changelog_version}."
        )
    error = check_bump(version, released)
    if error:
        raise ValueError(f"Invalid version bump: {error}")
    return version


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``validate-tag`` or ``notes-start-tag`` command.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
        Process exit code: 0 on success, 1 on a validation error.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-release-version")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-tag", help="Validate the release tag.")
    notes = commands.add_parser(
        "notes-start-tag", help="Print the tag a final release's notes start from."
    )
    notes.add_argument("--version", required=True, help="The version being released.")
    args = parser.parse_args(argv)

    tag_prefix = os.environ.get("TAG_PREFIX", "")
    released = released_versions(tag_prefix)

    if args.command == "notes-start-tag":
        print(notes_start_tag(args.version, released, tag_prefix) or "")
        return 0

    release_tag = os.environ.get("RELEASE_TAG") or os.environ.get("GITHUB_REF_NAME", "")
    if not release_tag:
        print("ERROR: No release tag found. Set RELEASE_TAG.", file=sys.stderr)
        return 1
    try:
        version = validate_tag(release_tag, tag_prefix, Path("CHANGELOG.rst"), released)
    except (ValueError, FileNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"INFO: Version validation passed for {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
