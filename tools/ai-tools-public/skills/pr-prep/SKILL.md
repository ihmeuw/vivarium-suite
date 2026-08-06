---
name: pr-prep
description: "Take a change you have already written from raw branch to a PR ready for review: multi-agent review, a per-finding disposition (fix now / ticket / drop), apply the fixes, re-validate, then organize the commits and open the draft PR. Use when the user says \"review this and open a PR\", \"prep this for review\", \"I'm done, ship it\", \"review my branch\", or \"code review\". For a feature not yet written use a development workflow instead; to split a diff you are not reviewing use commit-splitter; for branch, ticket, or PR mechanics alone use your team's conventions skill."
argument-hint: "Optional: a description of the change. Omit to work from the current branch."
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(simsci:_validator, simsci:_review_maintainability, simsci:_review_dry, simsci:_review_design, simsci:_review_tests, simsci:_review_documentation, simsci:_review_scorer)
---

Review the change on this branch and prepare it for review: $ARGUMENTS

This is the one-off counterpart to a development workflow's converge-and-ship
phases, for code that **already exists**. You review it, propose a disposition for
every finding, apply the fix-now set, confirm the tree is still green, and hand the
finish to the `simsci:_finalize-core` skill. **Exactly one user gate:** the PR,
inside `simsci:_finalize-core`. Every Jira write keeps its own approval, owned by
the ticket-filing skill.

It ends at a **draft PR** with the leftovers documented — not a merge, and not an
announcement. Marking the PR ready and telling the team stay deliberate acts.

## Step 1 — Establish the baseline

These are cheap `git` checks and two of them are hard stops, so they come before
the review — a fan-out spent on a mid-rebase repo is a fan-out thrown away.

1. **Require a clean tree.** Record `git status` and `git rev-parse HEAD` — that
   ref is the revert point for everything below. If the tree is dirty, ask the
   user to commit their work first; a single `WIP` commit is fine, because
   `simsci:_finalize-core` can reflow the history later. This is not fussiness:
   the per-finding commits in Step 4 are the recovery story, and a fix is only
   isolated if it is the only thing in its commit. Stop if the repo is mid-rebase
   or mid-merge.
2. **Fix the base.** Compute the merge-base with the default branch. **If you
   cannot, stop** — the lookup fails when `origin/<default-branch>` was never
   fetched, and the shell can be unavailable outright. Say which step failed and
   stop. Do **not** reconstruct the change from git internals, commit messages, or
   the current contents of the files and review that instead: a review of an
   inferred diff indicts code the change may never have touched, and every
   downstream step — the dispositions, the fixes, the PR — inherits that error
   while looking exactly like a real run.
3. **Find out whether the tree is already green.** Unless this session already saw
   the checks pass on this branch, dispatch `simsci:_validator` (inputs per Step 5)
   in the same turn as the Step 3 proposal, so it costs no extra round trip.
   Pre-existing failures are not yours, but you can only say so if you looked
   first.

## Step 2 — Gather the change and review it

The tree is clean as of Step 1, so the change is exactly
`git --no-pager diff <base>...HEAD`. Add `git log` for the branch's commit
messages. If the branch already has an open PR, pull its title and body for
context with your GitHub MCP server's pull-request tools — prefer the MCP over the
`gh` CLI when both are available: it needs no shell access and works in sandboxed
environments where `gh` cannot read its credentials.

Then invoke the `simsci:_review-core` skill, handing it the changed-file list, the
diff (or the salient slice), and a one-line description of the change as the
`<subject>`. Present the review it returns as-is — it owns the output format and
the review constraints.

## Step 3 — Propose a disposition per finding

Present every finding, nits included, in one table. Use the key
`simsci:_review-core` already assigned each one — never renumber and never invent a
code, because these keys travel into the commit subjects, the ticket drafts, and the
PR comment, and have to line up across all of them:

```
| Key  | Finding (file:line)                       | Conf | Do      | Why |
|------|-------------------------------------------|------|---------|-----|
| DES1 | engine.py:112 — collapse the two branches |  82  | fix now | in scope, one file |
| MNT3 | loader.py:40 — split the 90-line function |  88  | ticket  | pre-existing; bigger than this PR |
| NIT1 | utils.py:9 — docstring typo               |  55  | drop    | stylistic; not worth backlog space |
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

Print the table, then proceed — this is a plan you report, not a decision you hand
back. The table is the contract for Steps 4-6. If the user re-buckets a row
(`MNT3 -> fix now`, `all nits -> drop`) or adds a finding you missed, honor it
**without arguing** — if you think a move is wrong, say so in one clause and comply
in the same reply — then reprint the table and carry on from there.

## Step 4 — Apply the fix-now set

Edit directly in this session. The code, the tests, and the review all already
exist, so there is no black box to protect and no blind implementer to brief — a
development workflow routes fixes back to the sub-agent that wrote the code
precisely to keep it blind, and none of that applies here. What keeps it
reviewable instead:

1. **One finding at a time, one commit each**, in table order, subject
   `review: <key> — <what changed>` (e.g. `review: DES1 — collapse the two
   branches`). Never batch: the commit series *is* the audit
   trail, it localizes a later validation failure, and it turns a bad fix into a
   `git revert` rather than an unpicking job. It is scaffolding for this loop, not
   the shipping history — `simsci:_finalize-core` collapses it and regroups the diff
   anyway, so optimize it for recoverability here, not for how it reads.
2. **Stay inside the finding.** No opportunistic refactor, rename, or reformat,
   and nothing no row called for. The `file:line` in a row is an anchor, not the
   edit boundary — a DRY or design finding can take a few files to actually fix.
   What bounds you is the footprint that put it in **fix now**: a few files, no
   public-signature change, no new dependency. Scope any lint auto-fix to the
   files you edited.
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

On FAIL, attribute each failure against the Step 1 baseline:

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

## Step 6 — Finalize

Invoke the `simsci:_finalize-core` skill and follow it. Two things it can't work out
on its own: **the scope line is already drawn** by the Step 3 dispositions, and the
Step 1 ref is its **pre-apply ref** — anything blocked in Step 4 or reverted in
Step 5 belongs in the leftover set. If it is unavailable, report the three buckets
and the validation verdict and stop, leaving the branch in place.

## Constraints

- Review exactly once, at Step 2 — don't re-review after applying fixes; the
  budget in Step 5 is validation, not another review pass
- Never review an inferred diff. If the real one can't be computed, stop and say
  so rather than degrading to a guess that reads like a result
- No file change before Step 3's table is printed, and nothing outside it after
- No silent drops or promotions: every finding leaves this skill in exactly the
  bucket the printed table gave it
- Never `git reset --hard`, `git checkout -- .`, or force-push — the pre-apply ref
  and the per-finding commits are the entire recovery story
- The commit history and the PR are `simsci:_finalize-core`'s call; don't invoke a
  splitting skill from the apply phase
- Don't re-litigate a finding in prose: bucket it **drop** with its reason and let
  the user overturn that if they disagree
