---
name: _finalize-core
description: "Internal building block invoked by /simsci:pr-prep, /simsci:framework-development's Phase 5, and a model-development loop: takes work that is already reviewed and validated and prepares it for review — leftover-finding triage, the PR approval gate, a reviewable commit history, the draft PR, and the PR comment recording what was not addressed. Not an entry point — to review and prepare your own branch, use /simsci:pr-prep."
allowed-tools: Read, Grep, Glob, Bash, Agent(simsci:_split_proposer)
user-invocable: false
---

Finalize and open a PR for the work handed over in: $ARGUMENTS

This is the **finalize core** — the single definition of the back half every path
shares: triaging the findings that were deliberately left alone, gating on the
user's approval, shaping a reviewable history, opening the draft PR, and recording
in the PR what was *not* addressed. It is invoked **inline** by
`/simsci:pr-prep`, by `/simsci:framework-development`'s Phase 5, and by a
model-development loop.

Run it **inline, never as a forked sub-agent**, for three independent reasons: it
invokes `commit-splitter` (which spawns `simsci:_split_proposer`) and, where
installed, a ticket-filing skill (which spawns its own dedup agent), and a
sub-agent cannot spawn sub-agents; a forked agent cannot run the user gate this
unit is built around; and its commits must land in the caller's own tree.

**Not this unit's job** — the caller owns these: producing the review, deciding
which findings to address, applying the fixes, reaching a validation verdict, and
tearing down any worktrees or scratch state *before* handing over. Also
caller-specific and deliberately absent: posting domain artifacts to the PR, and
any stacked-PR ordering rule. Work only from the handoff in `$ARGUMENTS`; do not
review code, edit source, or re-litigate a disposition here.

**Where this stops.** A **draft** PR that exists, has a reviewable history behind
it, and carries the not-addressed comment. Marking it ready for review and telling
the team are deliberate acts the user takes — offer, never do them unasked.

## What the caller hands you

- The **addressed** set — what was fixed, and where it landed.
- The **leftover** set — findings deliberately not addressed, each with why — and
  whether the scope line is **already drawn** (see Step 2).
- The **dropped** set with reasons, if the caller distinguishes drops.
- The **validation verdict**, and the ref the work started from. Carry the verdict
  into the PR body as given; do **not** adjudicate whether work in this state may
  ship. That is the caller's policy and it already decided by arriving here — one
  caller carries residual failures forward deliberately, another refuses to
  finalize at all on exhaustion.
- **Hold-out paths**, if any — files that must be left uncommitted. Name them to
  `commit-splitter` as excluded, and report at the end that they are still
  uncommitted.
- The ticket key or feature description the PR body needs, and any PR partition
  rule (one PR, or a stack in a stated order).

Ask for anything missing rather than inferring it.

## Step 1 — Summarize and read the branch

State what was built or changed, the validation verdict, and anything carried out
of the caller's loop unresolved — a residual check failure, a fix that could not be
made green, a finding whose fix was blocked. This is the user's last look before
the gate, so surface the residuals here rather than letting them appear for the
first time in Step 6.

Then establish the git facts, changing nothing: `git status --short`, the upstream,
the base (`git merge-base HEAD origin/<default-branch>`), and
`git --no-pager log --oneline <base>..HEAD`. **A clean tree with nothing ahead of
the base means there is nothing to finalize** — say so and stop rather than opening
an empty PR.

## Step 2 — Triage the leftover findings

Skip entirely if nothing was left unaddressed.

If an installed skill covers filing tickets from review findings (e.g. a
ticket-triage skill), invoke it and follow it — it owns the classification, the
backlog dedup, the drafts, and the approval gate on every write; duplicate none of
that here. Hand it the leftover set, and when the caller reported the scope line as
**already drawn**, say so explicitly: what you are handing over is already the set
nobody is addressing in this PR, so it must not ask the user to draw that line a
second time. If it asks anyway, answer from the caller's partition rather than
bouncing the question back to the user.

If no such skill is installed, list the leftovers for the user as follow-up ticket
candidates and carry them unchanged into Step 6.

Either way, come out with a **disposition** per finding — a filed ticket key, "not
filed", or "unresolved" — because that is what Step 6 reports.

## Step 3 — Gate on the PR

**Gate — approve the PR.** Show in one place: Step 1's summary, the commit
organization you intend (Step 4), the PR title/base/draft status, and **the exact
body of the Step 6 comment**. Take one approval covering that whole write set —
commits, push, PR, and comment. The comment is a public write, so it gets seen
before it lands rather than after. Without approval, stop and leave the branch in
place, saying what would have happened.

## Step 4 — Shape a reviewable history

Branch on what Step 1 found:

1. **Uncommitted work in the tree** — `commit-splitter`'s native case. Invoke the
   `simsci:commit-splitter` skill and follow it, naming any hold-out paths as
   excluded and passing the partition rule in its brief; it owns the grouping
   proposal, its own confirmation gate, and its own backup ref. Commits already on
   the branch stay untouched.
2. **Committed, and the history already reads well** — commits are scoped and
   their subjects describe the change. Nothing to do; say so and move on. A
   coherent series is not improved by being rewritten, and `commit-splitter`
   refuses a clean tree by design.
3. **Committed, but the history does not read well** — one `WIP` blob, or
   unrelated changes bundled together. Don't rewrite it on your own initiative.
   Name the problem and offer, defaulting to the first:

   a. **Ship as-is**, noting the history in the PR body. A reviewer reads the whole
      diff anyway, so messy-but-shippable history is a style cost, not a
      correctness one.
   b. **Reword the tip commit** with `git commit --amend`, if it is unpushed.
   c. **Reflow**, only when explicitly asked: `git branch finalize-core-backup` at
      HEAD first — `git reset --soft` keeps every change but discards the original
      commit *messages*, so that ref is the only way back — then
      `git reset --soft <base>`, which puts the whole change in the tree as an
      uncommitted diff and is exactly `commit-splitter`'s input. Take branch 1 from
      there. **Refuse (c) outright** if any commit is already on the remote
      (unwinding it would need a force-push), if a rebase or merge is in progress,
      or if HEAD is detached.

Keep any backup ref until the user signs off in Step 6.

## Step 5 — Push and open the draft PR

Push the branch, then open the PR. If an installed skill covers your team's push/PR
conventions, invoke it and follow it rather than re-deriving them here; otherwise
fill the repo's PR template from the actual diff if one exists — a **draft** PR is
the safe default. Put Step 1's summary and the validation verdict, residuals
included, in the body. Prefer the GitHub MCP's pull-request tools over the `gh` CLI
when both are available: the MCP needs no shell access and works in sandboxed
environments where `gh` cannot read its credentials. **If the branch already has an
open PR, push to it and update its body** rather than opening a second one.

Note the PR number — Step 6 needs it, and so does a caller with something
domain-specific to attach once the PR exists.

## Step 6 — Post what was not addressed, then report

Every path posts this comment. It is the record that the gaps were decisions rather
than oversights, and it is why this unit owns the step instead of leaving it to each
caller. Post it on the PR with the GitHub MCP's issue-comment tool (a PR takes issue
comments), falling back to `gh pr comment <number>`. Skip only when all three
buckets below are genuinely empty.

Include, omitting any empty section:

- **Filed as tickets** — the keys/URLs from Step 2, one line each.
- **Not filed, deliberately** — the dropped findings with their one-line reasons.
- **Carried out unresolved** — a residual validation failure, a fix reverted
  because it could not go green, a finding whose fix was blocked. Say what it was
  and what stopped it.

Write it for a reviewer arriving cold. Don't restate the change, don't repeat the
PR body, and don't soften a leftover into sounding done.

Then report: the PR number and URL, the commits as they landed, any tickets filed,
and any hold-out paths still uncommitted. **Offer** to mark the PR ready for review
and announce it per your team's convention — if an installed skill covers that,
invoke it and follow it, but only on an explicit yes. Delete a Step 4 backup ref
once the user confirms things look right, or hand them the command.

## Constraints

- Don't adjudicate whether a red verdict may ship — carry it into the PR body and
  the Step 6 comment where a reviewer will see it. Whether to stop is the caller's
  policy, and the callers legitimately differ
- Never open an empty PR — a clean tree with nothing ahead of the base means stop
- Never mark a PR ready for review or post to a team channel unasked; the draft PR
  is where this unit stops
- Never `git reset --hard`, `git checkout -- .`, `git clean -f`, or force-push.
  Step 4's reflow is `--soft` only, gated, and refused on pushed commits. A branch
  needing real history surgery goes to `/simsci:git-rescue`, not here
- Do not edit source here. This unit shapes history and files reports; the fixes
  belonged to the caller
- Do not re-review the diff or re-litigate a disposition — the buckets arrive
  decided
- A hold-out path stays uncommitted, and the final report says so
- No silent omissions: every finding handed over as a leftover appears in Step 2's
  triage or Step 6's comment, or both, and never in neither
- Post the Step 6 comment even when every leftover was ticketed — "not addressed"
  covers work that was deferred, not just work that was dropped
