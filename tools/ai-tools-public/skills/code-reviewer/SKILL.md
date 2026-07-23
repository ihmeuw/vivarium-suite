---
name: code-reviewer
description: "Parallel multi-agent code review across maintainability, DRY, design, tests, documentation, and functional correctness."
argument-hint: "A pull request to review, or a description of the changes to review."
allowed-tools: Read, Grep, Glob, Bash, Agent(simsci:_review_maintainability, simsci:_review_dry, simsci:_review_design, simsci:_review_tests, simsci:_review_documentation, simsci:_review_scorer)
---

Run a parallel multi-agent code review of: $ARGUMENTS

This command gathers the review target, then hands it to the internal
`simsci:_review-core` skill (`skills/_review-core/SKILL.md`) for the fan-out. That
fan-out runs in this main-session context — `simsci:_review-core` invoked inline from
here spawns the specialists directly, kept one level deep by design. `simsci:_review-core` is the
single definition of the review, so it can be reused inline by other
main-session commands without duplicating the fan-out.

## Step 1 — Gather PR context

If $ARGUMENTS references a pull request (e.g. "#6", "PR 6", a GitHub URL),
use your GitHub MCP server's pull-request tools to fetch the PR title/body,
the diff, and the changed-file list (on the official GitHub MCP server,
`pull_request_read` with the `get`, `get_diff`, and `get_files` methods) —
plus `git log` for recent commit messages on the branch. Prefer the MCP
over the `gh` CLI when both are available: it needs no shell access and
works in sandboxed environments where `gh` may be unable to read its
credentials. Fall back to `gh pr view`/`gh pr diff` only if the GitHub
MCP is unavailable. Otherwise work from $ARGUMENTS as a free-form
description.

## Step 2 — Run the review

Invoke the `simsci:_review-core` skill, handing it the changed-file list, the diff
(or the salient slice), and a one-line description of the change (the PR
title/body is the `<subject>`). It fans out to the five `_review_*`
specialists, runs the functional-correctness pass, independently scores every
finding for confidence (dropping anything below 50), synthesizes the survivors —
each annotated with its confidence score — and returns the structured review.
Present that review to the user as-is — `simsci:_review-core` owns the output format and
the review constraints.

## Step 3 — Offer post-review triage (if a skill covers it)

After presenting the review, if it surfaced findings the user is not going
to address in the current PR **and** an installed skill covers filing
tickets from review findings (e.g. a ticket-triage skill), offer to run
that skill on them; if the user accepts, invoke it and follow it — don't
duplicate any of its classification or filing logic here. If no such
skill is installed, end after Step 2 — the presented review is the final
output.
