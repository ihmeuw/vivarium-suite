#!/usr/bin/env bash
# PostToolUse hook: after Claude pushes to a branch with an open PR, ask it to
# refresh the PR's test summary comments.
# hooks.json runs this only for Bash and the GitHub MCP push tools.
# Set SIMSCI_TEST_SUMMARY=off to disable it.
# On any problem, print nothing and exit 0, so the hook never blocks the tool call.
if [ "${SIMSCI_TEST_SUMMARY:-on}" = "off" ]; then
    exit 0
fi
command -v gh >/dev/null 2>&1 || exit 0
input=$(cat)

# Read one string field from the hook input. Values with escaped quotes are not
# handled, and the fields used here (branch, owner, repo) never have them.
field() {
    printf '%s' "$input" | sed -n "s/.*\"$1\" *: *\"\([^\"]*\)\".*/\1/p" | head -n 1
}

case "$(field tool_name)" in
    Bash)
        # A git push, also after options such as `git -C <dir> push`. Search the
        # raw input: the command can hold escaped quotes that `field` cannot read.
        printf '%s' "$input" | grep -Eq '(^|[;&|("] *)git( +-[^ ]+( +[^- "][^ "]*)?)* +push( |"|$)' || exit 0
        branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
        repo_args=()
        ;;
    *)
        branch=$(field branch)
        owner=$(field owner)
        repo=$(field repo)
        repo_args=()
        if [ -n "$owner" ] && [ -n "$repo" ]; then
            repo_args=(-R "$owner/$repo")
        fi
        ;;
esac
if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    exit 0
fi

pr=$(timeout 20 gh pr list "${repo_args[@]}" --head "$branch" --state open \
    --json number,url -q '.[0] | "#\(.number) (\(.url))"' 2>/dev/null)
[ -n "$pr" ] || exit 0

msg="You pushed to branch \`$branch\`, which has open pull request $pr. Its test summary comments may now be stale. Refresh them with the \`simsci:_test-summary-refresh\` skill before you report back, unless the current workflow posts them itself next (for example \`_finalize-core\` Step 5). Set SIMSCI_TEST_SUMMARY=off to turn this off."
printf '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%s"}}\n' "$msg"
