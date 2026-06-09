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
- **If the PR is in a model repo** (a concept-model implementation rather than
  the framework), check the changed code against the relevant research
  documentation using  the `vivarium-research` skill  and flag any place the implementation 
  diverges from the documented modelling strategy. If you can't determine the 
   relevant concept model, say so rather than guessing.

## Step 4 — Synthesize

When all five sub-agents return, merge their findings with your functional-
correctness review into the structured output below. Deduplicate findings
flagged by multiple sub-agents and attribute the perspective(s) that
flagged each issue.

## Output Format

**Omit any section that has no findings** — no empty headings, no "no issues
found" filler. A clean PR can be three lines. Spend words in proportion to
severity: a correctness bug earns a sentence of rationale; a nit gets none.

```
## PR Review: <title>

### Summary
<2-3 sentences on what the PR does — skip entirely if the title already says it>

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
<one line each: `file:line` — the fix. No rationale, no code block.>

### Overall
<one or two sentences, only if there's a cross-cutting theme worth naming. Omit if the findings already speak for themselves.>
```

Per-finding budget, scaled to severity:
- **Substantive findings** (Design through Functionality): `file:line`, then the
  problem and the fix in **≤2 sentences**. Add a "why it matters" clause only when
  the impact is non-obvious. Include a code snippet only when the fix isn't clear
  from a sentence — never to illustrate the problem.
- **Nits**: one line each, fix only.

## Constraints

- Do not suggest changes outside the scope of the PR diff
- Do not edit any files — this command is read-only and advisory
- Distinguish pre-existing issues encountered in changed code from issues introduced by the PR
- Be specific and actionable — avoid vague feedback like "this could be improved"
- No preamble and no recap of the diff. Do not restate what the code does — report only what should change. A reviewer should be able to read the whole review in under a minute.
- Omit a weak finding entirely rather than padding a section to look thorough
- `_review_tests` focuses solely on the test code and test coverage gaps — do not duplicate test findings in your functional correctness pass
- `_review_documentation` covers all forms of documentation (docstrings, comments, changelogs, READMEs) — do not duplicate documentation findings in the Maintainability section
