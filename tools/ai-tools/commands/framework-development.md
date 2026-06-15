---
description: "Guided design→implement→verify→PR loop for a well-scoped framework feature."
argument-hint: "A MIC ticket key, design doc, or description of the feature to build."
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(_test_writer, _feature_implementer, _validator, _review_maintainability, _review_dry, _review_design, _review_tests, _review_documentation)
---

Run an end-to-end framework development loop for: $ARGUMENTS

You (the main session) own the design and the stubs, then drive a **black-box
TDD** build: `_test_writer` and `_feature_implementer` produce the tests and the
implementation in isolation, and you fan out `_validator` and run the shared
`_review-core` skill for review (it fans out the five `_review_*` specialists).
The fan-out runs here because Claude sub-agents cannot spawn sub-agents. Work the
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
  subset, and defer single-caller abstractions. A groomed AC is still intent, not
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

## Phase 4 — Integrate and validate

1. **Commit each build worktree first.** The writer agents have no Bash, so their
   output is uncommitted in their worktrees — commit each (you have Bash) before
   integrating, or ``git checkout <branch> -- …`` pulls the bare stub.
2. Assemble the two disjoint lineages into the feature branch (``git checkout
   <branch>-impl -- <src paths>`` and ``<branch>-tests -- <test paths>``, or
   merge both). Reconcile rather than force-merge if a signature changed.
3. Spawn `_validator` with the package path, env, and targets (``make test-*``,
   ``make lint``, ``make mypy`` if the package ships ``py.typed``). It returns a
   compact PASS/FAIL report. On FAIL, go to Phase 6.

A working env from Phase 0 (the package importable, ``make`` targets runnable) is
a precondition here — if it can't be built, validation can't run, so resolve that
before reaching this phase rather than reporting a false PASS. With an **editable
install**, re-run ``make install`` from the integration checkout first so the env
imports the integrated code and not whichever worktree it was last installed from.

When auto-fixing lint (``black``/``isort``), scope it to the changed files — a
package-wide reformat sweeps unrelated files into the diff.

## Phase 5 — Review (shared review core)

Invoke the `_review-core` skill (`skills/_review-core/SKILL.md`), handing it the
integrated diff, the changed-file list, and a one-line description of the
feature. It fans out the five `_review_*` specialists and runs the
functional-correctness pass in this main-session context — the same definition
`/viv:code-reviewer` uses, so there is no duplication here — and returns the
synthesized findings. Carry those findings into the Phase 6 critic loop.

## Phase 6 — Generator/critic loop

Triage each failure and **re-dispatch to the owning agent in its existing
worktree** (the lineages stay separate, so the black box holds across rounds):

- **Implementation bug** → `_feature_implementer` in ``<impl_path>``, failure
  described in behavioral terms (input → expected output), never as test source.
- **Test bug** (asserts beyond the criteria, *or* an existing test that encodes
  now-superseded behavior the feature deliberately changes) → `_test_writer` in
  ``<tests_path>``. Existing-test breakage is common: a feature that changes
  observable behavior will trip tests that pinned the old behavior.
- **Spec gap** (legit behavior with no stub) → add the body-less stub to the
  contract, propagate to both worktrees, re-dispatch.

Re-integrate and re-validate each round. **Bound at ≤3 iterations**, then carry
residual issues into the Phase 7 summary.

## Phase 7 — Finalize and PR (gated)

1. Tear down the build worktrees and sub-branches (``git worktree remove``;
   tolerate a read-only ``.git`` in a sandbox).
2. Summarize what was built, the test results, and any residual issues.
3. **Triage leftover findings.** For review findings you deliberately did not
   address in this build (residual or out-of-scope after the Phase 6 loop),
   invoke the `ticket-triage` skill to classify them, dedup against the backlog,
   and file approval-gated Jira tickets. Skip if nothing is left unaddressed.
4. **Ask the user to approve the PR.** Without approval, stop and leave the
   branch in place.
5. On approval, use the `commit-splitter` skill to organize the work into clean,
   reviewable commits, then use `team-conventions` to push and ``gh pr create``
   with the repo's PR template; report the URL and offer the ``#vivarium_dev``
   flag.  Post a summary of the leftover findings from step 4 as a comment in the
   PR.
