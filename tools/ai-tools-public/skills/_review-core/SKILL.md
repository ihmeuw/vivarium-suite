---
name: _review-core
description: "Internal building block invoked by /simsci:pr-prep (and /simsci:framework-development's review phase): runs the multi-agent review fan-out + functional-correctness pass + per-finding confidence scoring + synthesis on a diff the caller has already gathered. Not a review entry point — to review a change, use /simsci:pr-prep."
allowed-tools: Read, Grep, Glob, Agent(simsci:_review_maintainability, simsci:_review_dry, simsci:_review_design, simsci:_review_tests, simsci:_review_documentation, simsci:_review_scorer)
user-invocable: false
---

Run the shared multi-agent review of: $ARGUMENTS

This is the **review core** — the single definition of the parallel fan-out, the
functional-correctness pass, the per-finding confidence scoring, and the
synthesis. It is invoked **inline** by `/simsci:pr-prep` (after it gathers the
context), and is designed to be reused the same way by other main-session
commands (e.g. a development workflow's review phase). Because it runs inline in
the caller's main-session context, its fan-out to the `_review_*` sub-agents
stays one level deep — so run this unit inline and **not** as a forked sub-agent.

**Not this unit's job** — the caller owns these: gathering the review target
(PR/diff context), and any follow-up action on the findings (e.g. filing
tickets). Work only from the review target handed to you in `$ARGUMENTS`; do not
fetch a PR or run git/gh here.

## Step 1 — Fan out to specialist sub-agents in parallel

In a single message, invoke ALL FIVE of the following sub-agents in
parallel. Hand each the same brief: the change description, the changed-file
list, and the diff (or the salient slice). Do this regardless of size or
content type — a docs-only change still goes through every review agent, and
sub-agents correctly report "no findings" if there are none.

- `simsci:_review_maintainability` — readability, documentation, implicit assumptions, coupling
- `simsci:_review_dry` — duplicated logic, missed abstractions, repeated patterns
- `simsci:_review_design` — data structure choices, algorithmic efficiency, API surface
- `simsci:_review_tests` — test coverage, test quality, edge cases
- `simsci:_review_documentation` — docstrings, comments, README/changelog updates

## Step 2 — Functional-correctness pass (in this session)

While the sub-agents run, perform your own functional-correctness review:

- Are there behavioral changes that may be unintentional or undocumented?
- Are edge cases handled (zero values, empty inputs, single-element collections)?
- Are type annotations consistent with actual runtime behavior?
- Are there silent data transformations (rounding, coercion) that could lose precision?
- **If an installed skill provides domain reference documentation** for the
  code under review (e.g. a research- or specification-docs skill), invoke it
  and check the changed code against the relevant documentation, flagging any
  place the implementation diverges from the documented behavior. If you can't
  determine the relevant reference, say so rather than guessing.

## Step 3 — Score every finding for confidence (independent, in parallel)

When the five review agents return, collect every finding — from all five review agents **and**
from your own functional-correctness pass — into one flat list, treating each as
a discrete item (including nits). Then have each one scored independently:

1. Gather the relevant `CLAUDE.md` paths once with Glob/Read: the repo-root
   `CLAUDE.md` (if any) plus any `CLAUDE.md` in directories the change touched.
2. In a single message, launch one `simsci:_review_scorer` **per finding**, in
   parallel. Hand each scorer: the one-line change description, the **single**
   finding (its `file:line`, the review agent that flagged it, the problem, and the
   proposed fix), the diff slice relevant to that finding, and the `CLAUDE.md`
   paths. Each returns a `SCORE` (0-100) and a one-line `WHY`.

Score your own functional-correctness findings the same way — the scorer is the
independent check on the review agent *and* on you, so don't exempt yourself.

## Step 4 — Filter by confidence

Drop every finding the scorer put **below 50**: scores of 0-25 (false positives
and unverified guesses) are discarded and never appear in the output. A finding
needs ≥50 — a verified-real issue — to survive. Count how many you dropped.

## Step 5 — Synthesize

Merge the surviving findings into the structured output below. When two review agents
flagged the same issue (two findings, two scores), collapse them into one entry,
attribute both review agents, and keep the **highest** of their scores. Annotate each
surviving finding with its confidence score.

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
<one line each: `file:line` — the fix *(confidence: NN)*. No rationale, no code block.>

### Overall
<one or two sentences, only if there's a cross-cutting theme worth naming. Omit if the findings already speak for themselves.>

<If any findings were dropped, end with one italic line: _Filtered N finding(s) below the confidence threshold (50)._>
```

Per-finding budget, scaled to severity:
- **Substantive findings** (Design through Functionality): `file:line`, then the
  problem and the fix in **≤2 sentences**, ending with `*(confidence: NN)*`. Add
  a "why it matters" clause only when the impact is non-obvious. Include a code
  snippet only when the fix isn't clear from a sentence — never to illustrate the
  problem.
- **Nits**: one line each, fix only, ending with `*(confidence: NN)*`.

## Constraints

- Score every finding independently before reporting it — a finding the `_review_scorer` puts below 50 is dropped, not softened or downgraded to a nit
- Do not suggest changes outside the scope of the diff
- Do not edit any files — this review is read-only and advisory
- Distinguish pre-existing issues encountered in changed code from issues introduced by the change
- Be specific and actionable — avoid vague feedback like "this could be improved"
- No preamble and no recap of the diff. Do not restate what the code does — report only what should change. A reader should be able to read the whole review in under a minute.
- Omit a weak finding entirely rather than padding a section to look thorough
- `_review_tests` focuses solely on the test code and test coverage gaps — do not duplicate test findings in your functional correctness pass
- `_review_documentation` covers all forms of documentation (docstrings, comments, changelogs, READMEs) — do not duplicate documentation findings in the Maintainability section
