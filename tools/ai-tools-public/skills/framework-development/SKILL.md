---
name: framework-development
description: "Guided design→implement→verify→PR loop for a well-scoped feature."
argument-hint: "A ticket key, design doc link, or feature description."
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(simsci:_test_writer, simsci:_feature_implementer, simsci:_validator, simsci:_review_maintainability, simsci:_review_dry, simsci:_review_design, simsci:_review_tests, simsci:_review_documentation, simsci:_review_scorer)
---

Run an end-to-end feature development loop for: $ARGUMENTS

You (the main session) own the design and the stubs, then drive a **black-box
TDD** build: `simsci:_test_writer` and `simsci:_feature_implementer` produce the tests and the
implementation in isolation, and you fan out `simsci:_validator` and run the shared
`/simsci:_review-core` skill for review (it fans out the five `simsci:_review_*`
specialists, then confidence-scores and filters their findings). Work the
phases in order; keep the user in the loop at the design and PR gates.

## Control flow

The phases below are the canonical detail; this is the skeleton:

```
setup                                # Phase 0: package, env, feature branch
design  = brainstorm with user       # Phase 1: incl. scope-tightening; user-gated
stubs   = author contract            # Phase 2: source + body-less test stubs; commit baseline
build impl_wt, test_wt               # Phase 3: simsci:_feature_implementer || simsci:_test_writer, isolated

# Phase 4 — converge: two gates, each with its own independent budget

Gate 1 — validate: up to 3 rounds, until green
    repeat:
        integrate impl_wt + test_wt
        validate  ->  PASS: green, exit gate
                  ->  FAIL: triage & re-dispatch each failure (impl bug / test bug / spec gap)
    still red after the budget  ->  carry residual failures to Phase 5, skip review (don't review red code)

Gate 2 — review: up to 3 rounds, until clean (separate budget from Gate 1)
    findings = review_core once       # full five-agent fan-out + correctness; always runs once on green
    repeat while must-fix findings remain:
        triage & re-dispatch each finding
        re-integrate, then re-validate    # a review fix can't silently break a test
        re-check each fix with the review agent that raised it
    leftover findings  ->  carry to Phase 5

finalize & PR                        # Phase 5: user-gated; residuals -> follow-up tickets
```

## Phase 0 — Setup

1. Locate the target package/project directory from $ARGUMENTS and ``cd`` there.
2. Activate the project's development environment — if an installed skill covers
   environment setup, use it; otherwise follow the project's docs.
3. Create a work branch per your team's naming conventions (if an installed
   skill covers them, invoke it and follow it; otherwise ask or use a sensible
   name) and **create the branch now**.

## Phase 1 — Design

- If $ARGUMENTS references a groomed ticket/design doc, fetch it via whichever
  ticket or wiki MCP is configured, or accept a pasted document.
- Then, if an installed skill covers structured brainstorming, invoke it and
  follow it; otherwise work through the design with the user directly.
- When the feature warrants a design document — whether or not a brainstorming
  skill handled the exploration: if an installed skill covers drafting your
  team's design documents, invoke it and follow it; otherwise follow your
  team's design-doc process, if any.
- Either way — including for a groomed ticket — run a
  **scope-tightening pass** before confirming: treat each acceptance criterion as
  intent to validate rather than literal law (flag any "every X" broader than the
  need), check whether a broadly-applied change should instead be a meaningful
  subset, and defer single-caller abstractions; surface any gap between a
  criterion's literal wording and the actual need to the user rather than
  building the wording.

Exit with a short written design summary the user has agreed to.

## Phase 2 — Stub the interface and the tests (inline)

Author **two stub layers** — the shared contract, which you own:

1. **Source stubs** — signatures + ship-quality API docstrings, bodies left as
   ``raise NotImplementedError`` / ``...``. Don't stuff test criteria into them.
2. **Test stubs** — the acceptance criteria as stubbed test functions:
   descriptive names + a one-line docstring each, **empty bodies**. This is
   where you decide *what* gets tested; the test writer only decides *how*.

Scope the contract to the **whole feature**, not just a new helper in isolation:
stub the call-site wiring (where the new code is invoked) alongside the new unit,
and include at least one integration-level test stub that exercises the feature
end-to-end. A unit that nothing calls is an incomplete feature.

Extend existing modules/tests rather than overwriting. Then **commit both** on
the feature branch — this body-less baseline is what lets the implementer see
the criteria without the assertions.

## Phase 3 — Black-box build (isolated worktrees, parallel)

Create two worktrees from the stub commit and keep them alive for the whole
loop, **never merged into each other** — the impl lineage only gains source
bodies, the test lineage only gains test bodies. Put them **inside the repo**
(under ``.claude/worktrees/``) so they're visible in your editor:

```
git worktree add -b <branch>-impl  .claude/worktrees/<branch>-impl  <stub-commit>
git worktree add -b <branch>-tests .claude/worktrees/<branch>-tests <stub-commit>
```

(If Bash runs sandboxed, worktrees inside the repo stay within the writable
workspace; note that ``git worktree add``/``commit`` also need the main repo's
``.git`` to be writable — verify your sandbox permits this before dispatching.)

**Dispatch both agents in one message** (parallel). Give each the design
summary, the source stubs + body-less test stubs and their paths, the
package/env, and the **absolute path of its own worktree** (the real path you
just created — not a placeholder) with a "work only inside it" instruction —
never the other's output.

- `simsci:_test_writer` (in ``<tests_path>``) fleshes out the test stub bodies;
  escalates any missing case instead of inventing one.
- `simsci:_feature_implementer` (in ``<impl_path>``) fills in the source stub bodies;
  the test stubs are read-only criteria it never fills, runs, or sees filled.

Neither changes a public signature; if a stub looks wrong it reports back and
you adjust the contract (re-stub, re-seed the worktrees).

## Phase 4 — Converge: validate, then review (the critic loop)

Integration, validation, and review run as two ordered gates — **validate**
(objective: tests/lint/types) then **review** (advisory: quality) — **each with
its own independent budget**, so exhausting the validation budget never eats into
the review budget. You advance to Phase 5 only when validation is green **and**
review is clean, or when a budget is exhausted (residuals carried forward, never
silently dropped). Review is **mandatory on every path** — a run never reaches the
PR gate unreviewed. A first-round validation failure is the *normal* TDD case
(the implementer fills bodies blind to the assertions), so it must not route
around review.

**Each round, in order:**

1. **Integrate.** Commit each build worktree first — the writer agents have no
   Bash, so their output sits uncommitted in their worktrees; commit each before
   integrating, or ``git checkout <branch> -- …`` pulls the bare
   stub. Then assemble the two disjoint lineages into the feature branch
   (``git checkout <branch>-impl -- <src paths>`` and ``<branch>-tests --
   <test paths>``, or merge both); reconcile rather than force-merge if a
   signature changed. With an **editable install**, re-run the project's
   editable-install command (a make target or equivalent) from the integration
   checkout first, so the env imports the integrated code and not
   whichever worktree it was last installed from.

2. **Gate 1 — validate.** Spawn `simsci:_validator` with the package path, env, and
   checks to run — the project's test, lint, and type-check commands (e.g. make
   targets or equivalents); it returns a compact PASS/FAIL report. A working env from Phase 0 (the package
   importable, the check commands runnable) is a precondition — if it can't be
   built, validation can't run, so resolve that rather than reporting a false
   PASS. **On FAIL, skip review this round** (don't review red code): triage and
   re-dispatch the fixes (below), then start the next round. When auto-fixing lint
   (e.g. with formatters like ``black``/``isort``), scope it to the changed files
   — a package-wide reformat sweeps unrelated files into the diff.

3. **Gate 2 — review (only on green).** Once validation passes, run review. The
   **first** time you reach green, invoke the `/simsci:_review-core` skill
   with the integrated diff, the changed-file list, and a one-line feature
   description — a full fan-out across the five
   **review agents** (one `simsci:_review_*` specialist per dimension: Design,
   Maintainability, DRY, Tests, Documentation) plus the functional-correctness
   pass in this main-session context, the same definition `/simsci:code-reviewer`
   uses. It then independently confidence-scores every finding (a `simsci:_review_scorer`
   per finding) and drops those below 50, so it returns the surviving findings
   bucketed by review agent — each annotated with its score — alongside your own
   Functionality pass. On a **later** green round, don't re-run the whole fan-out: re-dispatch
   each already-fixed finding **back to the review agent that raised it** for a
   resolved/not-resolved verdict. When no must-fix findings remain, run one final full `/simsci:_review-core`
   pass as the convergence check — it catches any *new* qualitative issue a fix
   introduced, which per-finding routing can't. A clean final pass means
   **converged** → go to Phase 5. Otherwise triage and re-dispatch (below), then
   start the next round.

**Triage and re-dispatch** each validation failure / review finding to the agent
that owns it, in its existing worktree (the lineages stay separate, so the black
box holds across rounds):

- **Implementation bug** → `simsci:_feature_implementer` in ``<impl_path>``, failure
  described in behavioral terms (input → expected output), never as test source.
- **Test bug** (asserts beyond the criteria, *or* an existing test that encodes
  now-superseded behavior the feature deliberately changes) → `simsci:_test_writer` in
  ``<tests_path>``. Existing-test breakage is common: a feature that changes
  observable behavior will trip tests that pinned the old behavior.
- **Spec gap** (legit behavior with no stub) → add the body-less stub to the
  contract, propagate to both worktrees, re-dispatch.

Re-validate after **every** fix — including review fixes — so a quality fix can't
silently break a test. **Two independent budgets**, neither counting the initial
build or the first validate/review: **≤3 validation rounds** to reach green, then
**≤3 review rounds** to reach clean. A review fix that breaks validation is
re-greened *within* its review round, so it counts against the review budget, not
the validation one. If the validation budget is exhausted before green, skip
review (don't review red code) and carry the residual failures forward; if the
review budget is exhausted, carry the residual findings forward. Either way the
residuals go into the Phase 5 summary, surfaced for the user rather than quietly
dropped.

## Phase 5 — Finalize and PR (gated)

1. Tear down the build worktrees and sub-branches (``git worktree remove``;
   tolerate a read-only ``.git`` in a sandbox).
2. Summarize what was built, the test results, and any residual validation
   failures or review findings carried out of the Phase 4 loop.
3. **Triage leftover findings.** For review findings you deliberately did not
   address in this build (residual or out-of-scope after the Phase 4 loop): if an
   installed skill covers filing tickets from review findings (e.g. a
   ticket-triage skill), invoke it and follow it; otherwise summarize them for
   the user as follow-up ticket candidates. Skip if nothing is left unaddressed.
4. **Ask the user to approve the PR.** Without approval, stop and leave the
   branch in place.
5. On approval, use the `/simsci:commit-splitter` skill to organize the work into clean,
   reviewable commits, then push the branch and open the PR — if an installed
   skill covers your team's push/PR conventions, invoke it and follow it;
   otherwise use the repo's PR template if one exists (a draft PR is a safe
   default). Report
   the URL and offer to announce the PR in your team channel. Post a summary of the leftover
   findings from step 3 as a comment in the PR.
