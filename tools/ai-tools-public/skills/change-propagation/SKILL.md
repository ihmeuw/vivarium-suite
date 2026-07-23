---
name: change-propagation
description: Propagate an adapted copy of a reference file or directory across several targets — one or more packages in one or more repositories — in parallel, then file one draft PR per repository. Use when the user wants to roll a piece of boilerplate or config out to many places at once. Not for a single-target edit (just edit it) and not when the change needs bespoke per-target design rather than adaptation of a shared reference.
---

# Change propagation

Take a **reference** — an existing file or small directory that already
embodies the thing you want everywhere — and propagate an **adapted** version
of it into a set of **targets**, in parallel, then file pull requests. A
target is a **package** (or shared root file) in a repository — the
reference's own repo and/or other GitHub repos. A repository may hold many
packages (a monorepo — discover its layout from the repo) or a single one, in
which case the package may simply be the repo root.
Adaptation is the point: each target's existing files are reconciled with the
reference and the source's own identity (package name, per-package config
values, paths, version) is rewritten, not necessarily copied verbatim.

You are the **lead**. You resolve the reference and the target list, fan out
one `simsci:_propagate_target` worker per target, converge their
proposals into per-repo branches, gate everything on one explicit approval,
and file the PRs.

## Process

You **MUST** complete these in order. Track them with `TaskCreate`.

### 1. Parse and confirm the input

Read `$ARGUMENTS` as natural language naming a **reference source** and a
**target list**. Resolve and classify, then **echo your parse back and wait
for confirmation before spawning anyone** — this is a cheap gate that catches
a misread reference or target before any work fans out.

- **Reference source** → a concrete file set. If it's a directory, list the
  files you'll carry. Identify the **source package/repo** so workers know
  what is source-specific versus the generalizable boilerplate.
- **Target list** → resolve each entry to a **repository plus a path within
  it**: a package name or path (`my-package`, `packages/core`) → the
  reference's own repo; an `owner/repo` (`my-org/some-repo`) → that repo (its
  root package, or a named path within it).
- **Per-target source material.** If adapting a target needs more than the
  shared reference — e.g. each target's *own* pre-migration file, recovered
  from an archived external repo or older git history — resolve that **here**,
  up front: workers get no GitHub tools and may be network-restricted — they
  should only ever need what you put in their brief (see step 2).
- **Optional ticket key.** If the user names a motivating ticket or issue,
  use it for branch names and PR linkage; otherwise proceed without one.

### 2. Fan out one worker per target

Spawn one `simsci:_propagate_target` worker per target, **in parallel**.
Brief each worker with: its single `target` (its repository and resolved
path within it), the `reference_files` (path + content), the
`source_package` note, the `intent`, the `check_command` when you know it,
and — when the adaptation needs material specific to that target — the
`target_basis` you gathered in step 1. Then:

- **Workers targeting the reference's own repo** each run in their own
  **isolated git worktree** — spawn them with the Agent tool's
  `isolation: "worktree"`, so the worker's working directory *is* a fresh
  checkout, cut from the same base you'll branch the integration branch from.
  They adapt into their package's directory (or a shared root file) and
  verify proportionate to the change — the repo's canonical check command for
  code, a parse + targeted-validity check for metadata-only edits (see the
  worker's step 4) — leaving the result in the worktree for you to integrate
  directly.
- **Workers targeting another repo** each work in a **local clone** you
  provision — the worker still gets a real checkout to edit in and to run the
  repo's checks against when an env is available.

### 3. Collect and converge

Each worker returns `{target, status, proposed files, verification,
conflicts, notes}`. Account for **every** target — a dead or garbled worker
is reported as *not propagated*. **Don't trust a worker's self-report
blindly** — sanity-check what it actually produced (the right file changed, the
content is plausible and complete, nothing truncated or dropped) before you
integrate it; a worker can report `adapted` while having mangled its own
output. For a correctness-critical propagation, an independent verification
pass per target — a second agent checking each adapted file against the intent
— is worth the cost. Then converge **by repository**: group workers by the
repo their target lives in and integrate each group onto a single branch of
that repo (their subtrees are disjoint, so they union cleanly) — one branch,
one PR, per repository. For **small, uniform, disjoint** edits — one field
added to each `pyproject.toml`, say — you needn't juggle N worktrees: a
worker can return its adapted content or a diff and you apply them onto one
branch yourself. Reserve full per-worktree integration for substantial,
multi-file per-target changes.

Sort targets into what you'll file versus what you'll hold:

- `adapted` → goes into the PR set.
- `no-op` (already matches the reference) → **no PR**; report as already
  current. Never open an empty PR.
- `conflict` → **held by default**; surface the divergence for a human call.
  The user can include it (apply anyway), drop it, or resolve it manually.
- `failed` / check-command failure → **held by default**; surface it.
  Distinguish an *adaptation-caused* failure (the change is bad) from a
  *pre-existing* one (the target was already broken — the user may want to
  propagate anyway). One bad target never blocks the rest.

A reference may also include a **shared root file** (root `pyproject.toml`
lint config, `CLAUDE.md`, a root workflow). Delegate it to its own worker like
any other target; integrate its worktree onto its repository's branch
alongside the per-package changes.

### 4. Present the plan and gate on one bulk approval

Show the consolidated plan in one place: per target, its status and proposed
change (diffs are fine to render); the held set (conflicts, failures, no-ops)
with reasons; and the PR set you intend to file (one **draft** PR per
repository). Then take **one bulk approval** to proceed.

### 5. (after approval) File the PRs

If an installed skill covers your team's PR conventions (e.g. a team
conventions skill), invoke it and follow it for the PR mechanics. Otherwise:
name branches descriptively (include the ticket key when there is one), fill
in each repo's PR template (`.github/pull_request_template.md`) when it has
one, and open every PR as a **draft**. File one PR per repository from its
integrated branch. If your team announces PRs in a chat channel and you have
a tool to post there, offer to do so.

### 6. Report

List, per target: the filed PR (number/URL) or the reason it was held (conflict,
failure, no-op, worker death). Confirm every target from step 1 is accounted
for. Stop — committing further, merging, and un-drafting are the user's call.
