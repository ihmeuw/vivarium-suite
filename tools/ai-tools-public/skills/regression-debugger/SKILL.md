---
name: regression-debugger
description: "Trace a regression across repositories to identify the behavioral change causing it."
argument-hint: "Describe the regression symptom, repos/branches involved, and any existing hypotheses."
allowed-tools: Read, Grep, Glob, Bash, Agent(simsci:_diff_analyzer, simsci:_hypothesis_tester)
---

Investigate a regression described by: $ARGUMENTS

The fan-out runs in this main-session context, with the slash command body
itself acting as the orchestration prompt.

## Phase 1 — Scope the Problem

Gather from $ARGUMENTS (and ask the user for anything missing):

- **Symptom**: What output or behavior is wrong, and in what direction? (e.g., "computed total too low", "aggregate metric off by 15%", "query returns empty results")
- **Repos**: Which repositories are involved?
- **Affected entities**: Which components, modules, or features are implicated?
- **Hypotheses**: Any suspicions about specific components?
- **When it broke**: known good/bad refs, an approximate date, or unknown

## Phase 2 — Identify the Change Boundary

The goal is to establish a **good ref** and a **bad ref** for each
relevant repository.

- **Two branches/commits given**: use them directly.
- **Approximate date**: `git log --after=... --before=... --oneline` to find candidate boundary commits.
- **Unclear or large boundary**: use `git bisect` (with `git bisect run` if a quick test exists, manual otherwise).
- **Multiple repos changed simultaneously**: narrow each repo independently, starting with the one most likely to contain the regression (application repo first, then library dependencies).

## Phase 3 — Analyze the Diffs (parallel fan-out)

For each repository with established good/bad refs, invoke a
`simsci:_diff_analyzer` sub-agent **in parallel** (one Agent call per repo, all
in a single message). For each, provide:

- The repo path
- The good and bad refs
- The regression symptom (so it can flag relevant changes)

Wait for all diff analyses to complete before proceeding.

## Phase 4 — Trace the Data Flow (in this session)

Starting from the affected output or behavior, trace backward through
the code to find where old and new behavior diverge:

1. Find the code that produces the affected metric
2. For each input or dependency, trace to its own source (data, other components, configuration)
3. Compare old vs new at each stage — read the old code via `git show <ref>:<path>` and the current code side by side
4. Repeat recursively until you find a stage where old and new behavior differ, or reach raw data/configuration

## Phase 5 — Form and Test Hypotheses (parallel fan-out)

From the diff analyses and data flow tracing, formulate specific
hypotheses. Then invoke a `simsci:_hypothesis_tester` sub-agent **in parallel**
for each hypothesis (one Agent call per hypothesis, all in a single
message). For each, provide:

- The hypothesis statement
- The repo path(s) and refs
- The specific files to examine

Collect all verdicts (CONFIRMED / REFUTED / INCONCLUSIVE) before
proceeding.

## Phase 6 — Report

Structure findings with these sections:

- **Summary**: 1-2 paragraph description of the regression and root cause (if found)
- **Confirmed Causes**: Numbered list of changes confirmed to alter behavior, with file references
- **Likely Causes**: Changes that are plausible but need runtime verification
- **Ruled Out**: Hypotheses tested and refuted, with reasoning
- **Recommended Verification Steps**: Specific runtime checks to confirm the diagnosis

## Common Pitfalls

When analyzing code built on a shared library or framework, always check:

- **Subclass overrides against the base-class signature.** A method that returns a value in the upstream library may return None in a downstream override if the subclass handles the data differently.
- **Shared data-transformation utilities.** Their signatures and behavior may have changed between refs.

If an installed skill provides domain reference documentation or known regression pitfalls for the code under investigation, invoke it and fold its guidance into the diff-analysis and hypothesis phases.

## Constraints

- Do not edit any files — this command is read-only and advisory
- Only review code that is part of the regression investigation (diffs, related files)
- Be specific and actionable — avoid vague feedback like "this could be improved"
- When sub-agents return overlapping findings, consolidate them and note which perspectives flagged the issue
