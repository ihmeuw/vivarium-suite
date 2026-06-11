---
description: "Parallel multi-lens code review across maintainability, DRY, design, tests, documentation, and functional correctness."
argument-hint: "A pull request to review, or a description of the changes to review."
allowed-tools: Read, Grep, Glob, Bash, Agent(_review_maintainability, _review_dry, _review_design, _review_tests, _review_documentation)
---

Run a parallel multi-lens code review of: $ARGUMENTS

This command gathers the review target, then hands it to the internal
`_review-core` skill (`skills/_review-core/SKILL.md`) for the fan-out. That
fan-out runs in this main-session context — Claude sub-agents cannot spawn
further sub-agents, so the `code_reviewer` orchestrator agent cannot do it on
its own, but `_review-core` invoked inline from here can. `_review-core` is the
single definition of the review, so it can be reused inline by other
main-session commands without duplicating the fan-out.

## Step 1 — Gather PR context

If $ARGUMENTS references a pull request (e.g. "#6", "PR 6", a GitHub URL),
use the GitHub MCP tools — `mcp__github__get_pull_request` for the title
and body, `mcp__github__get_pull_request_diff` (or
`get_pull_request_files`) for the diff and changed-file list — plus
`git log` for recent commit messages on the branch. Prefer the MCP over
the `gh` CLI: it works inside the sandbox (where `gh` cannot read its
credential file) and needs no shell. Fall back to `gh pr view`/`gh pr
diff` only if the GitHub MCP is unavailable. Otherwise work from
$ARGUMENTS as a free-form description.

## Step 2 — Run the review

Invoke the `_review-core` skill, handing it the changed-file list, the diff
(or the salient slice), and a one-line description of the change (the PR
title/body is the `<subject>`). It fans out to the five `_review_*` specialists,
runs the functional-correctness pass, synthesizes the findings, and returns the
structured review. Present that review to the user as-is — `_review-core` owns
the output format and the review constraints.
