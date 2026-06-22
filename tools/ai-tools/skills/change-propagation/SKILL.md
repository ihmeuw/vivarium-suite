---
name: change-propagation
description: Propagate an adapted copy of a reference file or directory across several targets — monorepo libs and/or external repos — in parallel, then file one draft PR per repo. Use when the user wants to roll a piece of boilerplate or config out to many places at once: "propagate this Makefile to config-tree, engine, and ihmeuw/vivarium", "roll this mypy block out across the libs", "apply libs/artifact's py.typed setup everywhere", "mirror this CI workflow to repos A, B, C". Not for a single-target edit (just edit it) and not when the change needs bespoke per-target design rather than adaptation of a shared reference.
---

# Change propagation

Take a **reference** — an existing file or small directory that already
embodies the thing you want everywhere — and propagate an **adapted** version
of it into a set of **targets**, in parallel, then file pull requests. The
targets are a mix of monorepo libs (`libs/<pkg>/`) and external GitHub repos.
Adaptation is the point: each target's existing files are reconciled with the
reference and the source's own identity (package name, `python_versions.json`,
paths, version) is rewritten — never copied verbatim.

You are the **lead**. You resolve the reference and the target list, fan out
one `_propagate_target` worker per target, converge their proposals into
per-repo branches, gate everything on one explicit approval, and file the
PRs. Workers are independent and write nothing durable — every branch, push,
and PR is yours, and none happens before the user says go.

## Process

You **MUST** complete these in order. Track them with `TaskCreate`.

### 1. Parse and confirm the input

Read `$ARGUMENTS` as natural language naming a **reference source** and a
**target list**. Resolve and classify, then **echo your parse back and wait
for confirmation before spawning anyone** — this is a cheap gate that catches
a misread reference or target before any work fans out.

- **Reference source** → a concrete file set. If it's a directory, list the
  files you'll carry. Identify the **source package/repo** so workers know
  what is source-specific (its name, `python_versions.json`, paths, version)
  versus the generalizable boilerplate.
- **Target list** → classify each entry:
  - a monorepo lib by name or path (`config-tree`, `libs/engine`) →
    `monorepo` substrate;
  - an `owner/repo` (`ihmeuw/vivarium`) → `external` substrate.
- **Optional MIC key.** If the user names a motivating ticket (`MIC-####`),
  use it for branch names and PR linkage (§5). If not, fall back to a
  descriptive branch and note that no ticket is linked.

If the user asked for a **dry run** (or you want to confirm the plan
cheaply), stop after this step having shown the resolved reference, the
classified targets, and — optionally — what each target's change *would* be,
with no worktrees, no writes, and no PRs. See "Dry-run mode".

### 2. Fan out one worker per target

Spawn one `_propagate_target` worker per target, **in parallel**. They are
**stateless and independent** — targets don't share a contract, so there is
no agent-team messaging (this is simpler than `/viv:type-hinter`; it's the
`ticket-triage` → `_duplicate_finder` fan-out shape, one worker per target).

Brief each worker with: its single `target`, the `substrate`, the
`reference_files` (path + content), the `source_package` note, and the
`intent`. Then:

- **Monorepo workers** each run in their own **isolated git worktree** — spawn
  them with the Agent tool's `isolation: "worktree"`, so the worker's working
  directory *is* a fresh checkout (cut from the same base you'll branch the
  integration branch from). They adapt into `libs/<pkg>/`, run the package's
  full `make check`, and report the changed files **as content**. The worktree
  is only a verification sandbox — you reconstruct the change from the report
  and apply it yourself, so you never read back from the worker's worktree and
  the harness can reclaim it.
- **External workers** work read-only through the GitHub MCP, adapt in memory,
  and report proposed file contents. They run **no checks and no writes** —
  the target repo's CI verifies after the PR.

Workers are **propose-only**. Nothing they do is durable.

### 3. Collect and converge

Each worker returns `{target, substrate, status, proposed files, verification,
conflicts, notes}`. Account for **every** target — a dead or garbled worker
is reported as *not propagated*, never silently dropped. Then converge by
substrate:

- **Monorepo → one PR.** Each worker is constrained to its own `libs/<pkg>/`,
  so monorepo targets touch disjoint subtrees and their changes union cleanly.
  Plan a **single** integration branch, cut from the **same base** as the
  workers' worktrees (current `origin/main`) so what they verified is what
  lands; after approval (in *File the PRs* below) you write every monorepo
  worker's proposed files onto it and open **one** `vivarium-suite` PR.
- **External → one PR per repo.** Each external target is its own repo and its
  own branch and PR.

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

**Shared-root caveat.** The disjoint-subtree union holds only for per-lib
files — workers are only ever given files that belong inside their
`libs/<pkg>/`. If the reference *also* includes a **shared root file** (root
`pyproject.toml` lint config, `CLAUDE.md`, a root workflow), that file is not
delegated to any worker: **you** apply it once, directly onto the integration
branch, reconciling it with what's there and surfacing a conflict rather than
overwriting a deliberate divergence. So the per-lib subtree changes still come
from workers and union cleanly; the single root-file change is yours.

### 4. Present the plan and gate on one bulk approval

Show the consolidated plan in one place: per target, its status and proposed
change (diffs are fine to render); the held set (conflicts, failures, no-ops)
with reasons; and the PR set you intend to file (one `vivarium-suite` PR +
one per external repo, each a **draft**).

Then take **one bulk approval** to proceed. A single "yes" files the whole PR
set. Draft status is the safety net — the user still reviews and un-drafts
each PR before merge (§6, §7). Nothing is pushed or filed before this yes.

### 5. (after approval) File the PRs

Route all PR mechanics through `/viv:team-conventions` — it owns branch
naming, the PR template, draft status, and the review flag; don't re-derive
them here. Per its §1 and §3:

- **Branch naming.** `<username>/<library>/mic-####/<short-desc>`. For the
  cross-cutting monorepo PR there's no single `<library>`, so omit that
  segment: `<username>/mic-####/propagate-<short-desc>`. External repos each
  get their own branch by the same convention. With no MIC key, use a
  descriptive branch and note no ticket is linked.
- **Monorepo PR.** Write every `adapted` monorepo worker's proposed files onto
  the one integration branch, commit, **push the branch first** (git, not the
  MCP), then `create_pull_request` with `draft: true` using
  `tools/ai-tools/.github/pull_request_template.md` (or the repo-root template
  for non-plugin changes).
- **External PRs.** For each external target, push the proposed files to its
  branch via the GitHub MCP `push_files`, then `create_pull_request` with
  `draft: true` and that repo's template. Use the GitHub MCP server matching
  the target's org (`ihmeuw` → public; `ihme-internal` → internal).
- **Write-denial fallback.** External-repo writes via the MCP can be
  auto-denied in a background session even after chat approval. If that
  happens, **don't fail the run** — hand the user the equivalent
  `! gh pr create --draft …` (and `gh api`/`git push`) commands to run
  themselves, keeping `--draft` so the manual path preserves the draft-PR
  safety net.

### 6. (optional, separately approved) Flag for review

Opening the drafts ends the automated run. Flagging for review is a
**separate, explicitly-approved** step, because it takes the PRs out of draft
(`team-conventions` §4): offer to post **one consolidated** `#vivarium_dev`
message linking all the PRs, and on approval take them ready and post. Do not
auto-flag — the user may want to eyeball each draft first.

### 7. Report

List, per target: the filed PR (key/URL) or the reason it was held (conflict,
failure, no-op, worker death). Confirm every target from step 1 is accounted
for. Stop — committing further, merging, and un-drafting are the user's call.

## Dry-run mode

When the user asks for a dry run (or to "just show the plan"), do steps 1 and
optionally a **read-only** pass of step 2 — workers resolve and adapt but the
lead applies nothing — then present the plan and stop. No worktrees beyond the
workers' own read scope, no integration branch, no writes, no PRs. This is the
cheap way to smoke-test a propagation before committing to a wide fan-out.

## Key disciplines

- **Approve before any durable write.** No push, branch, or PR before the one
  bulk approval. Show the full plan, wait, then write.
- **Adapt, never copy verbatim.** The source's name, versions, and paths get
  rewritten for each target. A propagation that carries them across is wrong.
- **Account for every target.** No silent drops: conflicts, failures, no-ops,
  and dead workers all appear in the plan and the report with a reason.
- **No empty PRs.** A target that already matches the reference opens nothing.
- **One bad target never blocks the rest.** Held targets are set aside; the
  good ones still ship.
- **Don't force conflicts.** A target with a deliberately different version is
  a human call, surfaced — not an overwrite.
- **YAGNI.** The input is always a pointed-at reference; there is no template
  catalog. Don't add one until a second real need appears.
- **Hand PR mechanics to `team-conventions`.** Branch naming, the PR template,
  draft status, and the `#vivarium_dev` flag live there — this skill calls it,
  it doesn't reimplement it.
