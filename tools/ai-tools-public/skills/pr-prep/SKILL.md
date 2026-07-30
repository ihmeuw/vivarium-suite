---
name: pr-prep
description: "Take a change you have already written from raw branch to a PR ready for review: multi-agent review, a per-finding disposition gate (fix now / ticket / drop), apply the approved fixes, re-validate, then organize the commits and open the draft PR. Use when the user says \"review this and open a PR\", \"prep this for review\", \"I'm done, ship it\", \"review my branch\", or \"code review\". For a feature not yet written use a development workflow instead; to split a diff you are not reviewing use commit-splitter; for branch, ticket, or PR mechanics alone use your team's conventions skill."
argument-hint: "Optional: a description of the change. Omit to work from the current branch."
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(simsci:_validator, simsci:_review_maintainability, simsci:_review_dry, simsci:_review_design, simsci:_review_tests, simsci:_review_documentation, simsci:_review_scorer)
---

Review the change on this branch and prepare it for review: $ARGUMENTS

This is the one-off counterpart to a development workflow's converge-and-ship
phases, for code that **already exists**. You review it, make the user decide
what to act on, apply what they approved, confirm the tree is still green, and
hand the finish to the `simsci:_finalize-core` skill. **Exactly two user gates:**
the dispositions here, and the PR inside `simsci:_finalize-core`.

It ends at a **draft PR** with the leftovers documented — not a merge, and not an
announcement. Marking the PR ready and telling the team stay deliberate acts.

## Step 1 — Gather the change and review it

Work out what changed: the merge-base with the default branch, then
`git --no-pager diff <base>...HEAD` plus `git --no-pager diff HEAD` for anything
uncommitted, and `git log` for the branch's commit messages. If the branch already
has an open PR, pull its title and body for context with your GitHub MCP server's
pull-request tools — prefer the MCP over the `gh` CLI when both are available: it
needs no shell access and works in sandboxed environments where `gh` cannot read
its credentials.

**If you cannot compute the diff, stop.** The merge-base lookup fails when
`origin/<default-branch>` was never fetched, and the shell can be unavailable
outright. Say which step failed and stop. Do **not** reconstruct the change from
git internals, commit messages, or the current contents of the files and review
that instead: a review of an inferred diff indicts code the change may never have
touched, and every downstream step — the dispositions, the fixes, the PR — inherits
that error while looking exactly like a real run.

Then invoke the `simsci:_review-core` skill inline, handing it the changed-file
list, the diff (or the salient slice), and a one-line description of the change as
the `<subject>`. It fans out the five `simsci:_review_*` specialists, runs the
functional-correctness pass, scores every finding for confidence and drops
anything below 50, and returns the synthesized review. Present it as-is —
`simsci:_review-core` owns the output format and the review constraints.

## Step 2 — Establish the baseline

1. **Require a clean tree.** Record `git status` and `git rev-parse HEAD` — that
   ref is the revert point for everything below. If the tree is dirty, ask the
   user to commit their work first; a single `WIP` commit is fine, because
   `simsci:_finalize-core` can reflow the history later. This is not fussiness:
   the per-finding commits in Step 4 are the recovery story, and a fix is only
   isolated if it is the only thing in its commit. Stop if the repo is mid-rebase
   or mid-merge.
2. **Find out whether the tree is already green.** Unless this session already saw
   the checks pass on this branch, dispatch `simsci:_validator` (inputs per Step 5)
   in the same turn as the proposal below, so it costs no extra round trip.
   Pre-existing failures are not yours, but you can only say so if you looked
   first.

## Step 3 — Propose a disposition per finding

Present every finding, nits included, in one table keyed by its section and number
in the review:

```
| #  | Finding (file:line)                       | Conf | Do      | Why |
|----|-------------------------------------------|------|---------|-----|
| D1 | engine.py:112 — collapse the two branches |  82  | fix now | in scope, one file |
| M3 | loader.py:40 — split the 90-line function |  64  | ticket  | pre-existing; bigger than this PR |
| N1 | utils.py:9 — docstring typo               |  55  | drop    | stylistic; not worth backlog space |
```

The **why** is one line about *the bucket*, never a restatement of the finding:
name the cause — in scope and bounded / exceeds this change's footprint /
pre-existing / speculative / needs a decision this skill can't make — and what
that means for this PR.

Default by **scope**, and use confidence only to break ties. Confidence says
whether a finding is *real*, and `simsci:_review-core` already dropped everything
below 50, so everything you are bucketing is real.

- **fix now** — inside the change's own footprint and bounded: a few files, no
  public-signature change, no new dependency.
- **ticket** — real but larger than the change: pre-existing code, another
  package, a design call, a signature change.
- **drop** — stylistic, speculative, or too trivial for backlog space, and
  nothing else. Never "we'd rather not". Every drop keeps its reason.
- Tie-break: at equal scope prefer **fix now** for the higher score; a 50-59
  finding needing code outside the change defaults to **ticket**.

A run where nothing lands in **fix now** is a normal outcome, not a failure — say
so plainly and go to Step 6 rather than manufacturing work.

**Gate — approve the dispositions.** The user may accept the table, re-bucket any
row (`M3 -> fix now`, `all nits -> drop`), add a finding you missed, or reject the
plan. Honor a re-bucketing **without arguing** — if you think a move is wrong, say
so in one clause and comply in the same reply. Reprint the final table: it is the
contract for Steps 4-6. **Change no file before this gate.**

## Step 4 — Apply the fix-now set

Edit directly in this session. The code, the tests, and the review all already
exist, so there is no black box to protect and no blind implementer to brief — a
development workflow routes fixes back to the sub-agent that wrote the code
precisely to keep it blind, and none of that applies here. What keeps it
reviewable instead:

1. **One finding at a time, one commit each**, in table order, subject
   `review: <#> — <what changed>`. Never batch: the commit series *is* the audit
   trail, it localizes a later validation failure, and it turns a bad fix into a
   `git revert` rather than an unpicking job.
2. **Stay inside the finding.** Change only what its approved row names — no
   opportunistic refactor, rename, or reformat, and no file no approved row
   mentions. Scope any lint auto-fix to the files you edited.
3. **Escalate instead of expanding.** A fix that turns out to need a
   public-signature change, a new dependency, or a design decision is marked
   **blocked**, left unapplied, and moved to the ticket set. Say so.
4. Finish with `git --no-pager diff <pre-apply-ref>..HEAD --stat` so the user sees
   the real footprint.

## Step 5 — Re-validate

Skip if Step 4 applied nothing. Otherwise dispatch `simsci:_validator` **once for
the whole applied set** — the per-finding commits are how you localize a failure,
so you do not need a suite run per fix. Give it the **package path** (the directory
holding the build/config file for the code you touched — in a monorepo the package
you edited, not the repo root), the **environment** to activate (follow an
installed environment-setup skill if one covers it, else the project's docs), and
the **checks**: the project's own test, lint, and type-check entry points. One
`_validator` per affected package, all dispatched in one message. A runnable env is
a precondition — a check that cannot run is a FAIL with the reason, never a PASS.

On FAIL, attribute each failure against the Step 2 baseline:

- **Your fix is wrong** → correct it, or `git revert` its commit.
- **The fix is right and an existing test pinned the old behavior** → changing an
  assertion is a scope escalation: explicit user approval only.
- **Red before you arrived** → not yours. Record it; spend no budget on it.

**Budget: ≤2 fix-and-re-validate rounds**, counting neither the baseline nor the
first run. These are small edits to code that was already green, so a second
consecutive failure means grinding will not help. Out of budget: **revert the
offending fix**, move its finding to the ticket set with the failure as its
evidence, re-validate, and carry on with the rest green — a fix that cannot go
green becomes a ticket, not a red PR. If the tree still will not go green after
reverting every fix you applied, **stop before Step 6** and surface it.

## Step 6 — Hand off to `simsci:_finalize-core`

Invoke the `simsci:_finalize-core` skill and follow it. Hand it the **addressed**
set (finding → commit), the **leftover** set (including anything blocked in Step 4
or reverted in Step 5, with why), the **dropped** set with reasons, the validation
verdict, the pre-apply ref, and the fact that **the scope line is already drawn**
by the Step 3 gate. Note that any fixes are already committed one per finding, so
its history step has only the user's pre-existing commits to consider. It owns the
triage, the PR gate, the commit history, the draft PR, and the not-addressed
comment — duplicate none of it here. If it is unavailable, report the three sets
and the verdict and stop, leaving the branch in place.

## Constraints

- Review exactly once, at Step 1 — don't re-review after applying fixes; the
  budget in Step 5 is validation, not another review pass
- Never review an inferred diff. If the real one can't be computed, stop and say
  so rather than degrading to a guess that reads like a result
- No file change before the Step 3 gate, and nothing outside the approved set
  after it
- No silent drops or promotions: every finding leaves this skill in exactly the
  bucket the printed table gave it
- Never `git reset --hard`, `git checkout -- .`, or force-push — the pre-apply ref
  and the per-finding commits are the entire recovery story
- The commit history and the PR are `simsci:_finalize-core`'s call; don't invoke a
  splitting skill from the apply phase
- Don't re-litigate a finding in prose: propose **drop** and let the user overturn
  it at the gate
