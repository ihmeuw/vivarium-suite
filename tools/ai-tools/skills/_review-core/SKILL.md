---
name: _review-core
description: "Internal building block invoked by /viv:code-reviewer (and framework-development's review phase): runs the multi-lens review fan-out + functional-correctness pass + synthesis on a diff the caller has already gathered. Not a review entry point — to review a PR, use /viv:code-reviewer."
allowed-tools: Read, Grep, Glob, Agent(_review_maintainability, _review_dry, _review_design, _review_tests, _review_documentation)
user-invocable: false
---

Run the shared multi-lens review of: $ARGUMENTS

This is the **review core** — the single definition of the parallel fan-out,
the functional-correctness pass, and the synthesis. It is invoked **inline** by
`/viv:code-reviewer` (after it gathers PR context), and is designed to be reused
the same way by other main-session commands (e.g. a development workflow's review
phase). Because it runs inline in the caller's main-session context, its fan-out
to the five `_review_*` sub-agents stays one level deep (a Claude sub-agent
cannot spawn sub-agents) — so this unit must **never** run as a forked sub-agent
(`context: fork`), or the fan-out would be nested and fail.

**Not this unit's job** — the caller owns these: gathering the review target
(PR/diff context), and any follow-up action on the findings (e.g. filing
tickets). Work only from the review target handed to you in `$ARGUMENTS`; do not
fetch a PR or run git/gh here.

## Step 1 — Fan out to specialist sub-agents in parallel

In a single message, invoke ALL FIVE of the following sub-agents in
parallel. Hand each the same brief: the change description, the changed-file
list, and the diff (or the salient slice). Do this regardless of size or
content type — a docs-only change still goes through every lens, and
sub-agents correctly report "no findings" if there are none.

- `_review_maintainability` — readability, documentation, implicit assumptions, coupling
- `_review_dry` — duplicated logic, missed abstractions, repeated patterns
- `_review_design` — data structure choices, algorithmic efficiency, API surface
- `_review_tests` — test coverage, test quality, edge cases
- `_review_documentation` — docstrings, comments, README/changelog updates

## Step 2 — Functional-correctness pass (in this session)

While the sub-agents run, perform your own functional-correctness review:

- Are there behavioral changes that may be unintentional or undocumented?
- Are edge cases handled (zero values, empty inputs, single-element collections)?
- Are type annotations consistent with actual runtime behavior?
- Are there silent data transformations (rounding, coercion) that could lose precision?
- **If the change is in a model repo** (a concept-model implementation rather
  than the framework), check the changed code against the relevant research
  documentation using the `vivarium-research` skill and flag any place the
  implementation diverges from the documented modelling strategy. If you can't
  determine the relevant concept model, say so rather than guessing.

## Step 3 — Synthesize

When all five sub-agents return, merge their findings with your functional-
correctness review into the structured output below. Deduplicate findings
flagged by multiple sub-agents and attribute the perspective(s) that
flagged each issue.

## Output Format

**Omit any section that has no findings** — no empty headings, no "no issues
found" filler. A clean change can be three lines. Spend words in proportion to
severity: a correctness bug earns a sentence of rationale; a nit gets none.

```
## Review: <subject>

### Summary
<2-3 sentences on what the change does — skip entirely if the subject already says it>

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

- Do not suggest changes outside the scope of the diff
- Do not edit any files — this review is read-only and advisory
- Distinguish pre-existing issues encountered in changed code from issues introduced by the change
- Be specific and actionable — avoid vague feedback like "this could be improved"
- No preamble and no recap of the diff. Do not restate what the code does — report only what should change. A reader should be able to read the whole review in under a minute.
- Omit a weak finding entirely rather than padding a section to look thorough
- `_review_tests` focuses solely on the test code and test coverage gaps — do not duplicate test findings in your functional correctness pass
- `_review_documentation` covers all forms of documentation (docstrings, comments, changelogs, READMEs) — do not duplicate documentation findings in the Maintainability section
