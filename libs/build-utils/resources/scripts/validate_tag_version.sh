#!/bin/bash
# Validate that the current git tag matches the version in CHANGELOG.rst
#
# This script is used to prevent accidental releases with incorrect version numbers.
# It's particularly important for GitHub repos where tags are created manually.
#
# Returns:
#   0 if versions match and are valid semver
#   1 otherwise

set -e

CHANGELOG_PATH="./CHANGELOG.rst"


# Check if CHANGELOG exists
if [ ! -f "$CHANGELOG_PATH" ]; then
    echo "ERROR: CHANGELOG file not found at $CHANGELOG_PATH"
    exit 1
fi

# Determine the tag to validate
TAG="${GITHUB_REF_NAME:-}"

# NOTE: This can be uncommented for debugging purposes in non-GitHub environments
# if [ -z "$TAG" ]; then
#     # Try to find a semantic version tag at the current commit.
#     # If multiple tags exist, find the one matching v#.#.# or #.#.#
#     TAG=$(git tag --points-at HEAD 2>/dev/null | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || echo "")
# fi

# If no tag found, fail
if [ -z "$TAG" ]; then
    echo "ERROR: No git tag found. Cannot validate version."
    exit 1
fi

# Extract version from tag (remove 'v' prefix if present)
TAG_VERSION=$(echo "$TAG" | sed 's/^v//')

# Validate that we got a semantic version from the tag
if ! echo "$TAG_VERSION" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+' > /dev/null; then
    echo "ERROR: Tag '$TAG' does not start with a semantic version (expected format: v#.#.# or #.#.#)"
    exit 1
fi

# Extract the first semantic version from CHANGELOG.rst
CHANGELOG_VERSION=$(grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' "$CHANGELOG_PATH" | head -n 1)

# Validate that we found a version in the CHANGELOG
if [ -z "$CHANGELOG_VERSION" ]; then
    echo "ERROR: Could not find a semantic version in $CHANGELOG_PATH"
    exit 1
fi

# Compare versions
if [ "$TAG_VERSION" != "$CHANGELOG_VERSION" ]; then
    echo ""
    echo "========================================"
    echo "ERROR: Version mismatch!"
    echo "========================================"
    echo "Git tag version:      $TAG_VERSION (from tag: $TAG)"
    echo "CHANGELOG version:    $CHANGELOG_VERSION"
    echo ""
    echo "Please ensure the git tag matches the latest CHANGELOG entry."
    echo "========================================"
    echo ""
    exit 1
fi

echo "INFO: Version validation passed: tag version ($TAG_VERSION) matches CHANGELOG version ($CHANGELOG_VERSION)"
exit 0
