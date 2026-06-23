#!/bin/bash
# Validate that the current git tag matches the version in CHANGELOG.rst
#
# This script is used to prevent accidental releases with incorrect version numbers.
# It's particularly important for GitHub repos where tags are created manually.
#
# Returns:
#   0 if versions match and are valid semver with strict one-step bumping
#   1 otherwise

set -e

is_strict_semver() {
    # Enforce major.minor.patch format
    local version="$1"
    echo "$version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
}

is_strict_one_step_bump() {
    local previous_version="$1"
    local current_version="$2"

    # Split the dot-seperated versions into major, minor, and patch components
    IFS='.' read -r prev_major prev_minor prev_patch <<< "$previous_version"
    IFS='.' read -r curr_major curr_minor curr_patch <<< "$current_version"

    # Patch bump: X.Y.Z -> X.Y.(Z+1)
    if [ "$curr_major" -eq "$prev_major" ] && [ "$curr_minor" -eq "$prev_minor" ] && [ "$curr_patch" -eq $((prev_patch + 1)) ]; then
        return 0
    fi

    # Minor bump: X.Y.Z -> X.(Y+1).0
    if [ "$curr_major" -eq "$prev_major" ] && [ "$curr_minor" -eq $((prev_minor + 1)) ] && [ "$curr_patch" -eq 0 ]; then
        return 0
    fi

    # Major bump: X.Y.Z -> (X+1).0.0
    if [ "$curr_major" -eq $((prev_major + 1)) ] && [ "$curr_minor" -eq 0 ] && [ "$curr_patch" -eq 0 ]; then
        return 0
    fi

    return 1
}

CHANGELOG_PATH="./CHANGELOG.rst"
if [ ! -f "$CHANGELOG_PATH" ]; then
    echo "ERROR: CHANGELOG file not found at $CHANGELOG_PATH"
    exit 1
fi

# Optional tag prefix for monorepo libs whose tags look like
# "vivarium-<lib>-vX.Y.Z" rather than "vX.Y.Z". Empty for standalone repos.
#
# Example: TAG_PREFIX="vivarium-core-" matches tags like "vivarium-core-v1.2.3".
# The previous-tag lookup below uses this to filter `git tag --list` so siblings
# in the same monorepo don't pollute the one-step-bump check.
TAG_PREFIX="${TAG_PREFIX:-}"

# Determine the tag to validate.
#
# RELEASE_TAG can be set explicitly by the caller (workflows triggered by push
# or workflow_dispatch don't have GITHUB_REF_NAME == the release tag, and the
# `GITHUB_*` namespace can't be overridden via a step `env:` block in GitHub
# Actions). Falls back to GITHUB_REF_NAME for workflows triggered directly by
# a tag (e.g. `release: types: [published]`), where it's already the tag.
RELEASE_TAG="${RELEASE_TAG:-${GITHUB_REF_NAME:-}}"
if [ -z "$RELEASE_TAG" ]; then
    echo "ERROR: No git tag found. Cannot validate version."
    exit 1
fi

# Extract version from tag: strip optional TAG_PREFIX, then optional 'v'.
RELEASE_VERSION="${RELEASE_TAG#$TAG_PREFIX}"
RELEASE_VERSION="${RELEASE_VERSION#v}"
if ! is_strict_semver "$RELEASE_VERSION"; then
    echo "ERROR: Tag '$RELEASE_TAG' must be strict semantic version format ${TAG_PREFIX}vX.Y.Z or ${TAG_PREFIX}X.Y.Z"
    exit 1
fi

# Extract the version token from the first changelog heading in format: "**..."
CHANGELOG_HEADING=$(grep -E '^\*\*' "$CHANGELOG_PATH" | head -n 1)
if [ -z "$CHANGELOG_HEADING" ]; then
    echo "ERROR: Could not find changelog heading in expected format '**...' in $CHANGELOG_PATH"
    exit 1
fi

CHANGELOG_VERSION=$(echo "$CHANGELOG_HEADING" | sed -En 's/^\*\*([0-9]+(\.[0-9]+)+)[^0-9.].*/\1/p')
if [ -z "$CHANGELOG_VERSION" ]; then
    echo "ERROR: Could not extract version from latest changelog heading: $CHANGELOG_HEADING"
    exit 1
fi
if ! is_strict_semver "$CHANGELOG_VERSION"; then
    echo "ERROR: Latest CHANGELOG version '$CHANGELOG_VERSION' is not strict semver (must be X.Y.Z)"
    exit 1
fi

# Compare versions
if [ "$RELEASE_VERSION" != "$CHANGELOG_VERSION" ]; then
    echo ""
    echo "========================================"
    echo "ERROR: Version mismatch!"
    echo "========================================"
    echo "Git tag version:      $RELEASE_VERSION (from tag: $RELEASE_TAG)"
    echo "CHANGELOG version:    $CHANGELOG_VERSION"
    echo ""
    echo "Please ensure the git tag matches the latest CHANGELOG entry."
    echo "========================================"
    echo ""
    exit 1
fi

# Validate one-step bump against the previous released git tag (not changelog order).
# When TAG_PREFIX is set, only tags matching the prefix participate.
PREVIOUS_RELEASE_VERSION=$( (
    git tag --list "${TAG_PREFIX}v*" 2>/dev/null | sed "s|^${TAG_PREFIX}||" | sed 's/^v//'
    echo "$RELEASE_VERSION"
) | while IFS= read -r version; do
    if is_strict_semver "$version"; then
        echo "$version"
    fi
done | sort -Vu | awk -v current="$RELEASE_VERSION" '
    $0 == current {print prev; found=1; exit}
    {prev = $0}
    END {if (!found) exit 1}
')

if [ -n "$PREVIOUS_RELEASE_VERSION" ]; then
    if ! is_strict_one_step_bump "$PREVIOUS_RELEASE_VERSION" "$RELEASE_VERSION"; then
        echo ""
        echo "========================================"
        echo "ERROR: Invalid version bump!"
        echo "========================================"
        echo "Previous release tag version: $PREVIOUS_RELEASE_VERSION"
        echo "Current release tag version:  $RELEASE_VERSION"
        echo ""
        echo "Expected exactly one semver step:"
        echo "  - patch bump: X.Y.Z -> X.Y.(Z+1)"
        echo "  - minor bump: X.Y.Z -> X.(Y+1).0"
        echo "  - major bump: X.Y.Z -> (X+1).0.0"
        echo "========================================"
        echo ""
        exit 1
    fi

    echo "INFO: Version validation passed: tag version ($RELEASE_VERSION) matches CHANGELOG version ($CHANGELOG_VERSION) with a valid one-step bump from previous release tag $PREVIOUS_RELEASE_VERSION"
else
    echo "INFO: Version validation passed: tag version ($RELEASE_VERSION) matches CHANGELOG version ($CHANGELOG_VERSION). No previous release tag found, so one-step bump check was skipped."
fi

exit 0
