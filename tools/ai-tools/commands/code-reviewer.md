---
description: "Parallel multi-lens code review across maintainability, DRY, design, tests, documentation, and functional correctness."
argument-hint: "A pull request to review, or a description of the changes to review."
allowed-tools: Read, Grep, Glob, Bash, Agent(_review_maintainability, _review_dry, _review_design, _review_tests, _review_documentation)
---

Run a parallel multi-lens code review of: $ARGUMENTS

The fan-out runs in this main-session context (Claude sub-agents cannot
spawn further sub-agents, so the `code_reviewer` orchestrator agent
cannot do this on its own — that's why this slash command exists).

## Step 1 — Gather PR context

If $ARGUMENTS references a pull request (e.g. "#6", "PR 6", a GitHub URL),
use `gh pr view`, `gh pr diff`, and `git log` to fetch the changed-file
list, the diff, the PR title and body, and recent commit messages on the
branch. Otherwise work from $ARGUMENTS as a free-form description.

## Step 2 — Fan out to specialist sub-agents in parallel

In a single message, invoke ALL FIVE of the following sub-agents in
parallel. Hand each the same brief: the PR title/body, the changed-file
list, and the diff (or the salient slice). Do this regardless of PR size
or content type — a docs-only PR still goes through every lens, and
sub-agents correctly report "no findings" if there are none.

- `_review_maintainability` — readability, documentation, implicit assumptions, coupling
- `_review_dry` — duplicated logic, missed abstractions, repeated patterns
- `_review_design` — data structure choices, algorithmic efficiency, API surface
- `_review_tests` — test coverage, test quality, edge cases
- `_review_documentation` — docstrings, comments, README/changelog updates

## Step 3 — Functional-correctness pass (in this session)

While the sub-agents run, perform your own functional-correctness review:

- Are there behavioral changes that may be unintentional or undocumented?
- Are edge cases handled (zero values, empty inputs, single-element collections)?
- Are type annotations consistent with actual runtime behavior?
- Are there silent data transformations (rounding, coercion) that could lose precision?

## Step 4 — Synthesize

When all five sub-agents return, merge their findings with your functional-
correctness review into the structured output below. Deduplicate findings
flagged by multiple sub-agents and attribute the perspective(s) that
flagged each issue.

## Output Format

```
## PR Review: <title>

### Summary
<1-2 paragraph description of what the PR does>

### Design
<numbered findings from _review_design>

### Maintainability
<numbered findings from _review_maintainability>

### DRY
<numbered findings from _review_dry>

### Tests
<numbered findings from _review_tests>

### Documentation
<numbered findings from _review_documentation>

### Functionality
<numbered findings from your own analysis>

### Minor Nits
<numbered findings>

### Overall
<brief assessment and key areas for improvement>
```

For each finding include the specific file:line reference, what the
issue is, why it matters, and a concrete suggestion (with code snippet
when helpful).

## Constraints

- Do not suggest changes outside the scope of the PR diff
- Do not edit any files — this command is read-only and advisory
- Distinguish pre-existing issues encountered in changed code from issues introduced by the PR
- Be specific and actionable — avoid vague feedback like "this could be improved"
- `_review_tests` focuses solely on the test code and test coverage gaps — do not duplicate test findings in your functional correctness pass
- `_review_documentation` covers all forms of documentation (docstrings, comments, changelogs, READMEs) — do not duplicate documentation findings in the Maintainability section
