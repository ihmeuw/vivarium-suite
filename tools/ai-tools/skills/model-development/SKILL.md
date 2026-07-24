---
name: model-development
description:
  "Guided iteration loop for a vivarium model — take one model change from a
  research-spec doc through a staged artifact→component→observer build,
  InteractiveContext verification, review, and a PR. Use when the user asks to
  implement or iterate a model concept (a cause, risk, intervention, or
  observer) in an existing model repo from its vivarium-research documentation,
  or names a MIC ticket that points at one."
argument-hint:
  "A MIC ticket key, a vivarium-research doc/section, or a description of the
  model iteration to build."
allowed-tools:
  Read, Grep, Glob, Bash, Edit, Write, Agent(_model_implementer, _vv_writer,
  _validator, _review_maintainability, _review_dry, _review_design,
  _review_tests, _review_documentation)
---

Run an end-to-end model-development loop for: $ARGUMENTS

You own the **iteration plan** — the contract that pins the artifact keys,
pipeline names, state-table columns, and observer output schema a change
touches, plus the quantitative expectations harvested from the research doc.
From that single contract you drive a **staged build** ordered by the model's
data-dependency chain (artifact → component → observer, skipping layers the
change doesn't touch) and, **in parallel**, a verification author that is blind
to the implementation. You fan out `_validator` and run the shared
`_review-core` skill for review. Work the phases in order; keep the user in the
loop at the plan, artifact-build, and PR gates.

**Scope.** You own the model-repo code for this iteration and its verification —
nothing else. Building or relocating the artifact is a user-gated hand-off (pause
and ask); a change to a core/framework repo (`vivarium`, `vivarium_public_health`)
is out of scope — surface it and stop, deferring to `/viv:framework-development`.

## Control flow

The phases below are the canonical detail; this is the skeleton:

```
setup                                 # Setup: model repo, env, feature branch; require a runnable env
plan  = iteration plan from research  # Plan: pin keys/pipelines/columns/outputs + expectations; user-gated

# Build and verification-authoring run in parallel off the approved plan:
build  = _model_implementer per layer    # Build: artifact -> [user gate: build artifact] -> component -> observer
verify = _vv_writer                   # Verify author: blind to the build — InteractiveContext checks + notebook

# Verify by running the simulation:
run _validator (existing suite) + new checks + local simulate + notebook  ->  traces
    artifact unavailable  ->  "unverified — sim checks not run"   # never a false PASS

# Critic loop, up to 3 rounds:
repeat while failures:
    impl bug   ->  re-dispatch the owning build stage (behavioral terms)
    check bug  ->  re-dispatch _vv_writer (weaken a check only with user approval)
    plan gap   ->  amend the contract, notify user, re-dispatch
    exhausted, still failing  ->  stop and surface to the user (do not review)

review = review_core                  # Review: five-lens fan-out + correctness; findings -> critic loop

finalize & PR                         # Finalize: post traces; artifacts uncommitted unless asked; user-gated PR(s), stacked per layer
```

## Setup

1. Resolve the target **model repo** from $ARGUMENTS (a `vivarium_*` model repo,
   e.g. `vivarium_gates_mncnh`) and `cd` there. This workflow runs in the model
   repo, not in `vivarium-suite`.
2. Activate the right conda env.
3. Create a **feature branch** for the iteration.

**Exit criterion — a runnable env.** Before leaving setup, confirm the repo
imports and the simulation entry points are on PATH. The verify step
_runs the model_, so a half-built env produces a false PASS later — resolve it
now rather than discovering it at verify time.

## Iteration plan (the contract)

- Pull the research requirements: if $ARGUMENTS references a MIC ticket or a
  vivarium-research page, fetch it. The research doc is the source of truth for
  _what the model should do_.
- Survey the repo before proposing changes: the components list and model spec
  YAML (`src/<pkg>/model_specifications/`), the artifact keys available to it,
  the existing components and observers, and the existing tests (`tests/`,
  including any `model_notebooks/` and V&V tests). Follow the existing structure
  — extend modules rather than inventing parallel ones.
- Then write the **iteration plan**, and have the user approve it. The plan is
  the contract every later phase builds from, so it must name things concretely:
  - **Layers touched** — which of artifact / component / observer this change
    needs, and which it deliberately leaves alone.
  - **Prerequisites** — anything the build depends on that this workflow won't
    produce: **new or changed artifact data** (you build/place it at the artifact
    gate) and any **core/framework change** (`vivarium`, `vivarium_public_health`
    — land it first via `/viv:framework-development` or by hand). Name them here
    so they are arranged up front, not discovered mid-build.
  - **Per-layer change list** — for each touched layer, the specific keys,
    pipelines, state-table columns, and observer outputs to add or change, by
    name. These names are what let the verification author work blind.
  - **Quantitative expectations** — the numbers the change should produce,
    lifted from the research doc: exposure distributions, relative risks, rates,
    coverage levels. This is what verification checks against.
  - **Non-goals** — what is explicitly out of scope for this iteration.

**Gate — approve the plan.** Exit the plan phase only with a written iteration plan
the user has agreed to — including any prerequisites it names (artifact data, a
core change).

## Staged build

Dispatch `_model_implementer` **once per touched layer**, in data-dependency
order, **skipping any layer the plan leaves alone**. Each stage works directly
on the feature branch — stages are sequential, so they cannot collide, and you
**diff-check each stage's output before dispatching the next** (the next stage
needs the real names the previous one produced, not the planned ones). **Commit
each stage as its own layer commit** (artifact / component / observer) so the
per-layer structure carries into the finalize-phase PRs.

Compose a **stage brief** for each dispatch from the plan: the layer-specific
guidance, the plan slice for that layer, the contract names to honor, exemplar
files in the repo to pattern-match, and the contract facts the previous stage
actually produced.

1. **Artifact** — `_model_implementer` writes the data-loading/artifact-building
   code (new keys, loaders). It does **not** build the artifact.

   **Gate (conditional) — the artifact build.** If this iteration changed
   artifact keys, the new data must exist before the simulation can run, and
   building it generally needs GBD/cluster data access. **Pause and ask the
   user** to build the artifact (or point the model spec at an updated one)
   before continuing. The workflow never rebuilds the artifact itself.

2. **Component** — `_model_implementer` writes the states, pipelines, and
   wiring that consume the artifact keys. May update model spec.

3. **Observer** — `_model_implementer` writes the outputs and stratification.
   May update model spec.


When a stage reports a forced deviation from the plan (a key or pipeline that
had to be named differently, a missing dependency), **update the contract** and
carry the corrected facts forward.

## Verification author (parallel with the build)

Because the verification author is **blind to the implementation by design**, it
does not wait for the build — dispatch `_vv_writer` **as soon as the
plan is approved**, concurrently with the build. Hand it the iteration plan (the contract
names and the quantitative expectations), pointers to the repo's existing test
patterns (`conftest.py` fixtures, `FuzzyChecker` from `vivarium.fuzzy_checker`,
the step/event mapping, `model_notebooks/`), and the target paths.

`_vv_writer` produces two things from the plan alone, both **internal to this
loop** (not committed by default — see the finalize phase):

- **InteractiveContext checks** following the repo's existing patterns — spin
  up the sim, advance to the relevant step, and assert the quantitative
  expectations against the state table / pipeline values.
- A **`model_<N>`-style verification notebook** (mirroring the repo's
  `model_notebooks/` convention) whose plots and tables are the **traces**
  posted to the PR.

## Verify

A runnable env and (for artifact-key changes) a built artifact are preconditions
— if either is missing, checks that cannot be executed must be surfaced to
the user.

1. Spawn `_validator` with the **model repo root** (the directory containing the
   Makefile — a model repo is a standalone repo, not a monorepo `libs/<pkg>`
   path), the env, and targets (`make test-*`, `make lint`, `make mypy` if the
   repo ships `py.typed`) for the repo's existing suite. It returns a compact
   PASS/FAIL report.
2. Run the new InteractiveContext checks, and a small **local `simulate` run**
   on a reduced spec/population to confirm the iteration completes end-to-end
   (a reduced-scope run is typically on the order of an hour — set that
   expectation with the user).
3. Execute the verification notebook to produce its **traces** (plots and
   tables) — for the user to eyeball against the research targets now, and to
   post to the PR at the finalize phase.

**Missing-artifact degradation.** If the artifact a sim check needs cannot be
produced in this environment, run everything that doesn't need it (lint, the
existing unit suite, review) and stop with an explicit **"unverified — sim
checks not run"** status. Leave the branch and checks in place to run where the
artifact exists. Never report PASS for a sim check that did not run.

## Critic loop (bounded)

Triage each failure and re-dispatch to the owner, **bounded at ≤3 iterations**:

- **Implementation bug** → `_model_implementer` for the owning layer, in its
  stage brief, with the failure in **behavioral terms** (scenario → expected vs.
  observed).
- **Check bug** (an assertion beyond the plan, a threshold that doesn't match
  the research doc) → `_vv_writer`. A verification check may only be
  **weakened** (threshold loosened, assertion dropped) with **explicit user
  approval** — the loop must not negotiate the checks down to make the build
  pass.
- **Plan gap** (legitimate behavior the plan never named) → amend the contract,
  notify the user, and re-dispatch the owning stage.

Re-verify each round. **On exhaustion with unresolved failures, stop and
surface them to the user — do not proceed to review or finalize.**

## Review

Invoke the `_review-core` skill with the integrated diff, the changed-file list,
and a one-line description of the iteration. Carry its findings into the critic-loop
triage.

## Finalize and PR (gated)

1. Summarize what was built, the verification results (including any
   "unverified" status), and any residual issues from the critic loop.
2. **Post the verification traces to the PR.** Attach the key plots, tables,
   and output from the verification notebook — the record that the iteration
   was checked against the research expectations.
3. **Don't commit the verification artifacts by default.** They are an
   internal loop for engineering confidence, not formal V&V (a separate
   research task). Ask whether to keep any in the repo (e.g. an
   InteractiveContext check kept as a regression test); commit only on an
   explicit yes.
4. **Triage leftover findings.** For review findings deliberately not addressed
   in this build, invoke the `ticket-triage` skill to classify them, dedup
   against the backlog, and file approval-gated Jira tickets. Skip if nothing is
   left unaddressed.
5. **Gate — approve the PR.** Without approval, stop and leave the branch in
   place.
6. On approval, use the `commit-splitter` skill to organize the work for review,
   then use `team-conventions` to push and `gh pr create` with the repo's PR
   template; report the URL(s) and offer the `#vivarium_dev` flag. **A multi-layer
   iteration ships as stacked per-layer PRs in data-dependency order (artifact ->
   component -> observer), matching team practice; a single-layer change is one
   PR.**
