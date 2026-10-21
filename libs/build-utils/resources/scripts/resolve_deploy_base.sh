#!/bin/bash
# Print the commit the deploy checks should diff HEAD against.
#
# A single push can land several commits at once (e.g. a rebased epic) and Jenkins
# builds only the tip, so diffing against HEAD~1 misses changes - such as the
# CHANGELOG.rst version bump - made in any earlier commit of that push. Diffing
# against the last successful build's commit covers the whole push.
#
# Environment:
#   CANDIDATE_BASE_COMMIT  Commit of the last successful build of this branch. May be
#                          unset or empty.
#
# The candidate is used when it resolves to a commit in this clone that is a strict
# ancestor of HEAD. Otherwise (unset, empty, missing from a shallow clone, rewritten
# history, or HEAD itself as on a forced rebuild) HEAD~1 is used, matching the
# pre-existing behavior.
#
# Output:
#   stdout  The full SHA of the resolved base commit.
#   stderr  One line naming which base was chosen and why.
#
# Returns:
#   0 if a base commit was resolved
#   1 if neither the candidate nor HEAD~1 is usable (e.g. HEAD is a root commit)

set -e

CANDIDATE_BASE_COMMIT="${CANDIDATE_BASE_COMMIT:-}"

if ! HEAD_SHA=$(git rev-parse --verify --quiet "HEAD^{commit}"); then
    echo "ERROR: Could not resolve HEAD" >&2
    exit 1
fi

reason=""
if [ -z "$CANDIDATE_BASE_COMMIT" ]; then
    reason="candidate base commit is unset or empty"
elif ! CANDIDATE_SHA=$(git rev-parse --verify --quiet "${CANDIDATE_BASE_COMMIT}^{commit}"); then
    reason="candidate $CANDIDATE_BASE_COMMIT not found in clone"
elif [ "$CANDIDATE_SHA" = "$HEAD_SHA" ]; then
    reason="candidate $CANDIDATE_SHA equals HEAD"
elif ! git merge-base --is-ancestor "$CANDIDATE_SHA" "$HEAD_SHA"; then
    reason="candidate $CANDIDATE_SHA is not an ancestor of HEAD"
else
    echo "INFO: Using last successful build commit $CANDIDATE_SHA as deploy base" >&2
    echo "$CANDIDATE_SHA"
    exit 0
fi

if ! PARENT_SHA=$(git rev-parse --verify --quiet "HEAD~1^{commit}"); then
    echo "ERROR: No usable deploy base: $reason, and HEAD has no parent" >&2
    exit 1
fi

echo "INFO: Using HEAD~1 ($PARENT_SHA) as deploy base: $reason" >&2
echo "$PARENT_SHA"
exit 0
