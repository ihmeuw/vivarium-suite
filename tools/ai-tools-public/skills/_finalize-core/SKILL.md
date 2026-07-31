---
name: _finalize-core
description: "Internal building block invoked by /simsci:pr-prep, /simsci:framework-development's Phase 5, and a model-development loop: takes work that is already reviewed and validated and prepares it for review — leftover-finding triage, the PR approval gate, a reviewable commit history, the draft PR, and the PR comment recording what was not addressed. Not an entry point — to review and prepare your own branch, use /simsci:pr-prep."
allowed-tools: Read, Grep, Glob, Bash, Agent(simsci:_split_proposer)
user-invocable: false
---

Finalize and open a PR for the work handed over in: $ARGUMENTS

This is the **finalize core** — the single definition of the back half every path
shares, from leftover triage through to the draft PR.

Run it **inline, never as a forked sub-agent**: it spawns sub-agents through
`commit-splitter` and the ticket-filing skill, it runs a user gate, and its commits
must land in the caller's own tree.

**Not this unit's job** — the caller owns these: producing the review, deciding
which findings to address, applying the fixes, reaching a validation verdict, and
tearing down any worktrees or scratch state *before* handing over. Also
caller-specific and deliberately absent: posting domain artifacts to the PR, and
any stacked-PR ordering rule. Work only from the handoff in `$ARGUMENTS`; do not
review code, edit source, or re-litigate a disposition here.

**Where this stops.** A PR that exists, has a reviewable history behind it, and
carries the not-addressed comment. A PR you open is a **draft**; a PR that was
already open keeps whatever state it had. Moving a draft to ready for review and
telling the team are deliberate acts the user takes — offer, never do them unasked.

## What the caller hands you

- The **addressed** set — what was fixed, and where it landed.
- The **leftover** set — findings deliberately not addressed, each with why — and
  whether the scope line is **already drawn** (see Step 2).
- The **dropped** set with reasons, if the caller distinguishes drops.
- The **validation verdict**, and the ref the work started from. Carry the verdict
  into the PR body as given; do **not** adjudicate whether work in this state may
  ship. Callers legitimately differ — one carries residual failures forward, another
  refuses to finalize on exhaustion — and each already decided by arriving here.
- **Hold-out paths**, if any — files that must be left uncommitted. Name them to
  `commit-splitter` as excluded, and report at the end that they are still
  uncommitted.
- The ticket key or feature description the PR body needs, and any PR partition
  rule (one PR, or a stack in a stated order).

Ask for anything missing rather than inferring it.

## Step 1 — Summarize and read the branch

State what was built or changed, the validation verdict, and anything carried out
of the caller's loop unresolved. This is the user's last look before the gate, so
surface the residuals here rather than letting them appear for the first time in
Step 6.

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

If Step 1 found **uncommitted work**, invoke the `simsci:commit-splitter` skill and
follow it, naming any hold-out paths as excluded and passing the partition rule in
its brief.

If the work is **already committed** — `commit-splitter` refuses a clean tree by
design — judge the history. Commits that are scoped, with subjects describing the
change, need nothing; say so and move on.

Otherwise (the single `WIP` commit a caller may have allowed at intake, or
unrelated changes bundled together), name the problem and **offer** to reflow it;
declining is a valid answer, since a reviewer reads the whole diff either way. On
a yes: `git branch finalize-core-backup` at HEAD first — `git reset --soft` keeps
every change but discards the original commit *messages*, so that ref is the only
way back — then `git reset --soft <base>`, which puts the whole change in the tree
as an uncommitted diff and is exactly `commit-splitter`'s input. Continue from the
paragraph above. **Refuse** if any commit is already pushed, if a rebase or merge
is in progress, or if HEAD is detached; those go to `/simsci:git-rescue`. Never
reflow unasked.

Keep any backup ref until the user signs off in Step 6.

## Step 5 — Push and open the PR

Push the branch, then land the PR. **If the branch already has an open PR, push to
it and update its body** rather than opening a second one — and leave its
draft-or-ready state exactly as you found it. Never demote a PR that is already
ready for review: that drops the review request and, on teams that signal readiness
by taking a PR out of draft, retracts the signal.

Opening a new one: if an installed skill covers your team's push/PR conventions,
invoke it and follow it rather than re-deriving them here; otherwise fill the repo's
PR template from the actual diff if one exists. A **draft** is the safe default for
a PR you open.

Either way, put Step 1's summary and the validation verdict, residuals included, in
the body. Prefer the GitHub MCP's pull-request tools over the `gh` CLI when both are
available: the MCP needs no shell access and works in sandboxed environments where
`gh` cannot read its credentials.

Note the PR number — Step 6 needs it, and so does a caller with something
domain-specific to attach once the PR exists.

## Step 6 — Post what was not addressed, then report

Every path posts this comment — it is the record that the gaps were decisions rather
than oversights. Post it on the PR with the GitHub MCP's issue-comment tool (a PR
takes issue comments), falling back to `gh pr comment <number>`. Skip only when all
three buckets below are genuinely empty; a leftover that was ticketed still counts
as not addressed.

Include, omitting any empty section:

- **Filed as tickets** — the keys/URLs from Step 2, one line each.
- **Not filed, deliberately** — the dropped findings with their one-line reasons.
- **Carried out unresolved** — a residual validation failure, a fix reverted
  because it could not go green, a finding whose fix was blocked. Say what it was
  and what stopped it.

Write it for a reviewer arriving cold. Don't restate the change, don't repeat the
PR body, and don't soften a leftover into sounding done.

Then report: the PR number and URL, the commits as they landed, any tickets filed,
and any hold-out paths still uncommitted. If the PR is still a draft, **offer** to
mark it ready for review and announce it per your team's convention — if an
installed skill covers that,
invoke it and follow it, but only on an explicit yes. Delete a Step 4 backup ref
once the user confirms things look right, or hand them the command.

## Constraints

- Never `git reset --hard`, `git checkout -- .`, `git clean -f`, or force-push. A
  branch needing real history surgery goes to `/simsci:git-rescue`, not here
- No silent omissions: every finding handed over as a leftover appears in Step 2's
  triage or Step 6's comment, or both, and never in neither
