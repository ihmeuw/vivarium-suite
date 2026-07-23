#!/usr/bin/env bash
# Prepare the current branch to enter the merge queue: squash its commits (unless
# it's a protected branch - see PROTECTED_BRANCH_PREFIXES in merge_prep.py) and
# correct the release date on any changed CHANGELOG to today (Pacific). Shared by
# the `merge-prep` make target (local) and the merge-prep workflow (CI).
#
# Env vars:
#   BRANCH   branch being prepared (default: current branch)
#   REMOTE   git remote (default: origin)
#   MESSAGE  squash commit message (default: the branch's first commit subject)
#   PUSH     force-push the result when "true" (default: true)
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
REMOTE="${REMOTE:-origin}"

git fetch "$REMOTE" main
BASE=$(git merge-base "$REMOTE/main" HEAD)

CHANGED_CHANGELOGS=$(git diff --name-only "$BASE" HEAD | grep 'CHANGELOG\.rst$' || true)

if python -m vivarium.build_utils.merge_prep should-squash "$BRANCH"; then
    echo "Squashing '$BRANCH' onto $BASE"
    MESSAGE="${MESSAGE:-$(git log --format=%s "$BASE"..HEAD | tail -1)}"
    git reset --soft "$BASE"
    # shellcheck disable=SC2086
    [ -n "$CHANGED_CHANGELOGS" ] && python -m vivarium.build_utils.merge_prep fix-changelog-dates $CHANGED_CHANGELOGS
    git add --all
    git commit -m "$MESSAGE"
else
    echo "'$BRANCH' is a protected branch; preserving commit history"
    if [ -n "$CHANGED_CHANGELOGS" ]; then
        # shellcheck disable=SC2086
        python -m vivarium.build_utils.merge_prep fix-changelog-dates $CHANGED_CHANGELOGS
        if ! git diff --quiet; then
            git add --all
            git commit -m "Correct CHANGELOG release date"
        fi
    fi
fi

if [ "${PUSH:-true}" = "true" ]; then
    git push --force-with-lease "$REMOTE" "HEAD:$BRANCH"
fi
