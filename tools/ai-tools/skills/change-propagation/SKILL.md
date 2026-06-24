---
name: change-propagation
description: Propagate an adapted copy of a reference file or directory across several targets — monorepo libs and/or external repos — in parallel, then file one draft PR per repo. Use when the user wants to roll a piece of boilerplate or config out to many places at once. Not for a single-target edit (just edit it) and not when the change needs bespoke per-target design rather than adaptation of a shared reference.
---

# Change propagation

Take a **reference** — an existing file or small directory that already
embodies the thing you want everywhere — and propagate an **adapted** version
of it into a set of **targets**, in parallel, then file pull requests. The
targets are a mix of monorepo libs (`libs/<pkg>/`) and external GitHub repos.
Adaptation is the point: each target's existing files are reconciled with the
reference and the source's own identity (package name, `python_versions.json`,
paths, version) is rewritten, not necessarily copied verbatim.

You are the **lead**. You resolve the reference and the target list, fan out
one `_propagate_target` worker per target, converge their proposals into
per-repo branches, gate everything on one explicit approval, and file the
PRs.

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
- **Target list** → classify each entry:
  - a monorepo lib by name or path (`config-tree`, `libs/engine`) →
    `monorepo` substrate;
  - an `owner/repo` (`ihmeuw/vivarium`) → `external` substrate.
- **Per-target source material.** If adapting a target needs more than the
  shared reference — e.g. each target's *own* pre-migration file, recovered
  from an archived external repo or older git history — resolve that **here**,
  while you (the lead) still have GitHub/network access. Workers don't; they
  only ever see what you put in their brief (see step 2).
- **Optional MIC key.** If the user names a motivating ticket (`MIC-####`),
  use it for branch names and PR linkage. If not, offer to file one.

### 2. Fan out one worker per target

Spawn one `_propagate_target` worker per target, **in parallel**. Brief each
worker with: its single `target`, the `substrate`, the `reference_files`
(path + content), the `source_package` note, the `intent`, and — when the
adaptation needs material specific to that target — the `target_basis` you
gathered in step 1. **Workers have no network or GitHub access**, so any
per-target source that lives outside the monorepo (an archived repo's
historical `setup.py`, a prior config, external file state) you must fetch and
hand them; a worker cannot research it itself. Then:

- **Monorepo workers** each run in their own **isolated git worktree** — spawn
  them with the Agent tool's `isolation: "worktree"`, so the worker's working
  directory *is* a fresh checkout, cut from the same base you'll branch the
  integration branch from. They adapt into `libs/<pkg>/` (or a shared root
  file) and verify proportionate to the change — full `make check` for code, a
  parse + targeted-validity check for metadata-only edits (see the worker's
  step 4) — leaving the result in the worktree for you to integrate directly.
- **External workers** each work in a **local clone** of the target repo that
  you provision — it's outside the monorepo, but the worker still gets a real
  checkout to edit in and to run the repo's checks against when an env is
  available.

### 3. Collect and converge

Each worker returns `{target, substrate, status, proposed files, verification,
conflicts, notes}`. Account for **every** target — a dead or garbled worker
is reported as *not propagated*. **Don't trust a worker's self-report
blindly** — sanity-check what it actually produced (the right file changed, the
content is plausible and complete, nothing truncated or dropped) before you
integrate it; a worker can report `adapted` while having mangled its own
output. For a correctness-critical propagation, an independent verification
pass per target — a second agent checking each adapted file against the intent
— is worth the cost. Then converge by substrate: one PR for the monorepo
(integrate every monorepo worker's worktree onto a single `vivarium-suite`
branch — their subtrees are disjoint, so they union cleanly), and one PR for
each external repo. For **small, uniform, disjoint** edits — one field added to
each `pyproject.toml`, say — you needn't juggle N worktrees: a worker can
return its adapted content or a diff and you apply them onto one branch
yourself. Reserve full per-worktree integration for substantial, multi-file
per-target changes.

Sort targets into what you'll file versus what you'll hold:

- `adapted` → goes into the PR set.
- `no-op` (already matches the reference) → **no PR**; report as already
  current. Never open an empty PR.
- `conflict` → **held by default**; surface the divergence for a human call.
  The user can include it (apply anyway), drop it, or resolve it manually.
- `failed` / `make check` failure → **held by default**; surface it.
  Distinguish an *adaptation-caused* failure (the change is bad) from a
  *pre-existing* one (the lib was already broken — the user may want to
  propagate anyway). One bad target never blocks the rest.

A reference may also include a **shared root file** (root `pyproject.toml`
lint config, `CLAUDE.md`, a root workflow). Delegate it to its own worker like
any other target; integrate its worktree onto the same `vivarium-suite` branch
alongside the per-lib changes.

### 4. Present the plan and gate on one bulk approval

Show the consolidated plan in one place: per target, its status and proposed
change (diffs are fine to render); the held set (conflicts, failures, no-ops)
with reasons; and the PR set you intend to file (one `vivarium-suite` PR +
one per external repo, each a **draft**). Then take **one bulk approval** to proceed.

### 5. (after approval) File the PRs

Hand all PR mechanics to `/viv:team-conventions` — branch naming, the PR
template, draft status, and the `#vivarium_dev` review flag live there. File
one `vivarium-suite` PR for the integrated monorepo branch and one PR per
external repo.

### 6. Report

List, per target: the filed PR (key/URL) or the reason it was held (conflict,
failure, no-op, worker death). Confirm every target from step 1 is accounted
for. Stop — committing further, merging, and un-drafting are the user's call.
