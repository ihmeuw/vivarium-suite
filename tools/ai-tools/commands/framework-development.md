---
description: "Guided design→implement→verify→PR loop for a well-scoped framework feature."
argument-hint: "A MIC ticket key, design doc, or description of the feature to build."
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(_test_writer, _feature_implementer, _validator, _review_maintainability, _review_dry, _review_design, _review_tests, _review_documentation)
---

Run an end-to-end framework development loop for: $ARGUMENTS

You (the main session) own the design and the stubs, then drive a **black-box
TDD** build: `_test_writer` and `_feature_implementer` produce the tests and the
implementation in isolation, and you fan out `_validator` and run the shared
`_review-core` skill for review (it fans out the five `_review_*` specialists). Work the
phases in order; keep the user in the loop at the design and PR gates.

## Phase 0 — Setup

1. Resolve the target ``libs/<pkg>`` package from $ARGUMENTS and ``cd`` there.
2. Use the `environments` skill to activate the right conda env.
3. Use the `team-conventions` branch convention (from the MIC key, or ask) and
   **create the branch now**.

## Phase 1 — Design

- If $ARGUMENTS references a groomed ticket/design doc, fetch it (Jira MCP
  ``get_issue`` / hub ``get_page``).
- Then, invoke the `brainstorming` skill (and `design-doc` if it warrants a
  design document).
- Either way — including for a groomed ticket — run the `brainstorming` skill's
  **scope-tightening pass** before confirming: treat each acceptance criterion as
  intent to validate rather than literal law (flag any "every X" broader than the
  need), check whether a broadly-applied change should instead be a meaningful
  subset, and defer single-caller abstractions. A groomed acceptance criterion is still intent, not
  law; surface any gap to the user rather than building the literal wording.

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

(Under the Bash sandbox these live in the writable workspace, and the main
repo's ``.git`` is already writable for linked worktrees — so ``git worktree
add``/``commit`` work. If your worktrees dir sits outside the session root, add
it to ``sandbox.filesystem.allowWrite``.)

**Dispatch both agents in one message** (parallel). Give each the design
summary, the source stubs + body-less test stubs and their paths, the
package/env, and the **absolute path of its own worktree** (the real path you
just created — not a placeholder) with a "work only inside it" instruction —
never the other's output.

- `_test_writer` (in ``<tests_path>``) fleshes out the test stub bodies;
  escalates any missing case instead of inventing one.
- `_feature_implementer` (in ``<impl_path>``) fills in the source stub bodies;
  the test stubs are read-only criteria it never fills, runs, or sees filled.

Neither changes a public signature; if a stub looks wrong it reports back and
you adjust the contract (re-stub, re-seed the worktrees).

## Phase 4 — Converge: validate, then review (the critic loop)

Integration, validation, and review run as **one bounded loop** with two gates in
order — **validate** (objective: tests/lint/types) then **review** (advisory:
quality). You advance to Phase 5 only when validation is green **and** review is
clean, or when the iteration cap is hit (residuals carried forward, never
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
   signature changed. With an **editable install**, re-run ``make install`` from
   the integration checkout first, so the env imports the integrated code and not
   whichever worktree it was last installed from.

2. **Gate 1 — validate.** Spawn `_validator` with the package path, env, and
   targets (typically ``make test-*``, ``make lint``, and ``make mypy`` if typed);
   it returns a compact PASS/FAIL report. A working env from Phase 0 (the package
   importable, ``make`` targets runnable) is a precondition — if it can't be
   built, validation can't run, so resolve that rather than reporting a false
   PASS. **On FAIL, skip review this round** (don't review red code): triage and
   re-dispatch the fixes (below), then start the next round. When auto-fixing lint
   (``black``/``isort``), scope it to the changed files — a package-wide reformat
   sweeps unrelated files into the diff.

3. **Gate 2 — review (only on green).** Once validation passes, run review. The
   **first** time you reach green, invoke the `_review-core` skill
   (`skills/_review-core/SKILL.md`) with the integrated diff, the changed-file
   list, and a one-line feature description — a full fan-out across the five
   review **lenses** (one `_review_*` specialist per dimension: Design,
   Maintainability, DRY, Tests, Documentation) plus the functional-correctness
   pass in this main-session context, the same definition `/viv:code-reviewer`
   uses. It returns findings bucketed by lens, alongside your own Functionality
   pass. On a **later** green round, don't re-run the whole fan-out: re-dispatch
   each already-fixed finding **back to the lens that raised it** for a
   resolved/not-resolved verdict. When no must-fix findings remain, run one final full `_review-core`
   pass as the convergence check — it catches any *new* qualitative issue a fix
   introduced, which per-finding routing can't. A clean final pass means
   **converged** → go to Phase 5. Otherwise triage and re-dispatch (below), then
   start the next round.

**Triage and re-dispatch** each validation failure / review finding to the agent
that owns it, in its existing worktree (the lineages stay separate, so the black
box holds across rounds):

- **Implementation bug** → `_feature_implementer` in ``<impl_path>``, failure
  described in behavioral terms (input → expected output), never as test source.
- **Test bug** (asserts beyond the criteria, *or* an existing test that encodes
  now-superseded behavior the feature deliberately changes) → `_test_writer` in
  ``<tests_path>``. Existing-test breakage is common: a feature that changes
  observable behavior will trip tests that pinned the old behavior.
- **Spec gap** (legit behavior with no stub) → add the body-less stub to the
  contract, propagate to both worktrees, re-dispatch.

Re-validate after **every** fix — including review fixes — so a quality fix can't
silently break a test. **Bound at ≤3 corrective iterations** (the initial build
and the first validate/review are not counted); on exhaustion, carry the residual
validation failures and review findings into the Phase 5 summary, so they are
surfaced for the user rather than quietly dropped.

## Phase 5 — Finalize and PR (gated)

1. Tear down the build worktrees and sub-branches (``git worktree remove``;
   tolerate a read-only ``.git`` in a sandbox).
2. Summarize what was built, the test results, and any residual validation
   failures or review findings carried out of the Phase 4 loop.
3. **Triage leftover findings.** For review findings you deliberately did not
   address in this build (residual or out-of-scope after the Phase 4 loop),
   invoke the `ticket-triage` skill to classify them, dedup against the backlog,
   and file approval-gated Jira tickets. Skip if nothing is left unaddressed.
4. **Ask the user to approve the PR.** Without approval, stop and leave the
   branch in place.
5. On approval, use the `commit-splitter` skill to organize the work into clean,
   reviewable commits, then follow `team-conventions` to push the branch and open
   the PR with the repo's PR template. Report
   the URL and offer the ``#vivarium_dev`` flag. Post a summary of the leftover
   findings from step 3 as a comment in the PR.
