---
name: commit-splitter
description: Split a bulk uncommitted diff into a sequence of small, reviewable commits — and, when scope warrants, into multiple PR-sized branches. Use when the user asks to break up uncommitted changes for review, or references PR-size limits. May also be surfaced as a suggestion when a large pending diff is about to be committed — but offer, don't impose, since a coherent refactor often should stay as one commit.
---

# Commit splitter

Agents tend to produce large, internally-consistent diffs because verification loops require everything to work together. That's fine for correctness but bad for review. This skill takes such a diff and doles it out into reviewable commits, and when needed into multiple PR-sized branches, without losing the working state that already verified.

## When NOT to use

- The diff is a coherent refactor whose intermediate states wouldn't compile, pass tests, or make sense to a reviewer in isolation. One commit is correct.
- Hunks are tightly entangled inside the same files such that any split would produce commits that don't build. Surface this and ask the user whether to proceed anyway, keeping the explanation visible at answer time (see step 3).
- The user is mid-implementation and not done. Splitting before the work is verified end-to-end risks freezing a broken intermediate state.

## Workflow

### 1. Capture the working state

```bash
git status
git diff --stat HEAD
git diff HEAD                # full unified diff, for the subagent
```

Refuse to proceed if the working tree is clean. If the user is mid-rebase, mid-merge, or has detached-HEAD state, stop and ask before touching anything.

### 2. Propose a grouping (subagent)

Delegate the grouping to the `viv:_split_proposer` specialist agent. It has read-only access to the working tree and returns a structured plan — commit groups (subject, rationale, files, dependencies), a PR partition, and any inseparable hunks. Keeping the file-by-file reading inside the subagent keeps the main context clean.

The skill does not need to repeat the agent's prompt — the agent file owns it. Invoke it with a short brief that names the repo and any constraints the user has already stated (e.g. "user wants two PRs", "stay under 300 lines per PR"). If no constraints are given, the agent defaults to a single PR with multiple commits unless scope forces otherwise.

### 3. Confirm the plan with the user

Surface the proposed plan and let the user accept, edit, or reject it. The plan must be visible at answer time: put it in an `AskUserQuestion` (question text / option previews), or end the turn with the plan and collect the answer in chat — mid-turn text written before a tool call may not be displayed in background-job sessions. At minimum, ask:

- Does the commit grouping look right? (accept / edit / abandon)
- Single PR with multiple commits, or multiple PRs?

If the subagent flagged inseparable hunks, raise those explicitly — don't bury them.

### 4. Execute commits (main thread)

Run the commits in the main thread so the user sees each one happen.

First, **create a backup branch** at the current `HEAD` so the entire pre-split state is recoverable:

```bash
git branch commit-splitter-backup        # marker at HEAD before any commits
```

Because the splitting steps below only *add* and *commit* (never discard), `git reset --soft commit-splitter-backup` collapses every new commit back into the working tree, restoring the exact original diff if anything goes wrong. Delete the branch in step 6 once the user has signed off.

- **File-aligned groups** (most common): `git reset HEAD` to unstage everything, then for each group: `git add <files>` → `git commit -m "<subject>" -m "<body>"`. Leftover changes stay in the working tree until the next group claims them.
- **Hunk-aligned groups within a file**: `git add -p <file>` for the user to walk through interactively, *or* draft the hunks into a patch and `git apply --cached`. Hunk splits are fiddly; prefer file-aligned splits when possible.
- After each commit: `git --no-pager log -1 --stat` so the user can verify before the next group lands.

If a commit fails (lint hook, type check), stop and surface the failure. Do not `--no-verify`. Either fix in place and amend with explicit user consent, or roll the commit back with `git reset --soft HEAD~1` and re-plan.

### 5. Branch and PR (hand off)

When the plan calls for multiple PRs, each PR needs its own branch. Defer branch naming, ticket linkage, PR body, and pinging Slack to `viv:team-conventions` — don't re-derive them here. Typical shape:

- **One PR, many commits:** stay on the current branch; push and open a single PR.
- **Multiple PRs:** for each group, create a branch off the appropriate base (usually `main`; sometimes the prior PR's branch if there's a hard dependency), cherry-pick the relevant commits onto it, push, and open the PR. Call out the dependency chain in each PR body so reviewers know the merge order.

### 6. Clean up

Ask for sign-off with a final-message summary of the commit/PR layout — the per-commit narration in step 4 is mid-turn output the user may not have seen. Once the user has confirmed the commits and PRs look right, delete the safety branch: `git branch -D commit-splitter-backup`. Don't delete it earlier — it's the recovery path if a split needs to be unwound. If the user is unsure, leave it in place and tell them the command to remove it later.

## Safety constraints

- Never discard unstaged changes. Never `git checkout -- .`, `git clean -f`, or `git reset --hard` to "tidy up" the working tree.
- Never force-push. Never skip hooks.
- If something looks wrong mid-flow (commits not landing, hooks failing, hunks not applying), stop and ask. The user's working state is the source of truth.
- Do not edit code as part of splitting. The skill rearranges history; it does not change behavior. The commits and branches you produce must, taken together, reproduce the original working tree exactly — nothing dropped, duplicated, or modified.
- Keep the `commit-splitter-backup` branch until the user signs off (step 6). It is the recovery path: `git reset --soft commit-splitter-backup` restores the full pre-split diff.
