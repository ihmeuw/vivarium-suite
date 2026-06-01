---
description: "Type-hint a target — a whole package, a sub-folder, or individual files — until it conforms to the package's mypy config. Runs as the lead of an agent team: resolves the inter-file dependency graph, spawns one autonomous teammate per file, and lets teammates negotiate shared type contracts directly. Requires Claude Code agent teams (experimental)."
argument-hint: "What to type-hint: a package or sub-folder path, a glob, or individual .py files — all within one libs/<pkg>/."
allowed-tools: Read, Edit, Grep, Glob, Bash, Agent(_type_hint_file)
---

Type-hint the target described in: $ARGUMENTS

You are the **lead of an agent team**. Each target file is owned by one
autonomous teammate (`_type_hint_file`) that runs in its own context,
runs `make mypy` itself, and resolves its own file. Teammates that share
a type contract coordinate **directly with each other** through the team
mailbox rather than routing every cross-file decision through you. Your
job: bootstrap shared config, work out the dependency graph so each
teammate knows whom to talk to, do the final verification, and own
everything the user must sign off on.

## Step 0 — Preflight the team feature

Agent teams are experimental and opt-in. Confirm that agent subteams are configured via tool availability.
There is no subagent fallback, so don't emulate the
team with one-shot sub-agents. Point the user at `/viv:plugin-setup` to
enable it.

## Step 1 — Resolve the target

Interpret `$ARGUMENTS` as a description of what to type-hint — a whole
package or directory, a glob, or one or more individual `.py` files.
Expand it into the concrete **target set** of `.py` files:

- a directory or package → every `.py` under it, recursively;
- a glob → its matches;
- explicit paths → those files.

Verify the set is non-empty and every member is an existing `.py` file.
Test files (`tests/**`) are in scope — hint them like any other file.

All resolved files must live under a single `libs/<pkg>/` — `make` targets
are per-package. Set `PKG_ROOT` to that directory.

## Step 2 — Probe the package

Read `${PKG_ROOT}/pyproject.toml`. If it lacks a `[tool.mypy]` block with
`mypy_path = "src"` and `explicit_package_bases = true`, add it: copy the
`libs/artifact/pyproject.toml` precedent verbatim, **omitting** its
`tables` `[[tool.mypy.overrides]]`. Announce the diff and proceed — don't
gate on it; the user wants the skill to bootstrap missing config. The
block must exist before teammates spawn, since each runs `make mypy`
against it.

## Step 3 — Capture the baseline

Run `make mypy` from `${PKG_ROOT}` once and record the error count in
**non-target** files. That count is pre-existing noise the workflow must
not regress; you check against it in step 8.

## Step 4 — Resolve the inter-file dependency graph

Before spawning anyone, work out how the **target files** depend on one
another, so each teammate is told up front whom to coordinate with. This
is what stops two teammates inventing divergent contracts. Using
`Read` / `Grep`, within the target set only, find:

- **Import edges**: B imports a symbol from A → `A → B`.
- **Shared-symbol edges**: a type alias, class, `TypedDict`, protocol, or
  function defined in A and referenced in B. Note the symbol.
- **Contract edges**: B consumes the return value of a function in A, so
  B's annotations depend on A's return type.

Derive each file's **upstream** (contracts it consumes) and **downstream**
(consumers). For each shared symbol, decide **which file owns it**
(default: the file that defines it today; if new, the most upstream file
in its cluster). Print the graph to the user as a short adjacency summary
(file → depends-on → owns) before any edits.

## Step 5 — Seed the shared task list

Create the team's shared task list:

- One task per target file: "type-hint `<file>` to mypy-clean".
- For each edge `A → B`, mark B's task blocked-by A's for the *shared
  contract* only — B can annotate its independent surfaces in parallel.
- For each shared symbol with more than one consumer, add a task "agree
  contract for `<symbol>`" owned by the owning teammate.

Teammates self-claim their file task and watch their contract tasks.

## Step 6 — Spawn the team

Spawn one `_type_hint_file` teammate per target file as a **team**
(persistent and able to message each other — not one-shot sub-agents).
Brief each with: `file`; `package` (`${PKG_ROOT}`); `target_files` (the
set plus each teammate's name); `upstream` (files/symbols it consumes and
the owning teammate); `downstream` (consumers and the symbols they take);
`owned_symbols`; and a `baseline` note that `make mypy` covers the whole
package, so it must filter output to its own file.

Then let the team run. As lead you:

- **Monitor** the task list and mailbox.
- **Answer routing questions** — if a teammate finds a cross-file edge you
  missed, record it, update the graph, and loop in the right teammates.
- **Collect, don't pre-empt.** Let teammates settle contracts themselves;
  intervene only on a deadlock, then make the call.

Teammates surface — but don't act on — anything needing the shared
`pyproject.toml` or a user decision: proposed overrides, logic-change
candidates, `# type: ignore` proposals. Hold these for step 7.

## Step 7 — Reconcile with the user

When teammates report clean (or blocked only on decisions), walk the
unresolved items with the user, in order:

1. **External-package overrides**: present each proposed
   `[[tool.mypy.overrides]]` with its mypy error and the precedent
   (`libs/artifact/` overrides `tables`). Apply only with sign-off, to
   `${PKG_ROOT}/pyproject.toml` — **you** own this file; teammates never
   edit it.
2. **Logic-change candidates**: present each with file:line, the current
   code, and what mypy expects. Never apply unilaterally. If the user
   declines, offer a `# type: ignore` (code + reason) or to leave the
   error. Route an accepted fix back to the owning teammate, or apply it
   yourself if the team is already torn down.
3. **`# type: ignore` candidates**: present each (file:line, code,
   justification); apply only the accepted ones.

## Step 8 — Final verification and `py.typed`

Run a final `make mypy` and `make check` over the whole package. Confirm
zero errors in target files and a non-target error count `<=` the step-3
baseline. If either fails, return to step 7 (or re-engage the relevant
teammate). Don't proceed until this passes, or until the only remaining
errors are ones the user explicitly accepted.

Then decide `py.typed`: **if and only if this final run reports zero
errors for the whole package** (not just the target files — accepted ones
left on the floor count as errors), the package is now fully typed, so add
the marker at `${PKG_ROOT}/src/.../py.typed`, alongside the package's
`__init__.py` (match a sibling typed package such as `libs/artifact/`).
If any error remains, leave `py.typed` absent — the package isn't fully
typed and this run is a partial conversion. The marker turns on CI mypy
for the package, so it must be honest.

## Step 9 — Report

Leave all changes in the working tree — don't stage or commit. Print:

- Files touched, hint count, and owning teammate.
- The step-4 graph and the contracts settled (symbol → owner → consumers →
  agreed type).
- mypy delta (baseline → final).
- Items the user accepted (declined logic changes, accepted ignores,
  applied overrides).
- Whether step 2 bootstrapped `[tool.mypy]`, and whether step 8 added
  `py.typed` (if not, note the package is still a partial conversion).

Then point the user at `/viv:commit-splitter` to dole the diff into
reviewable commits.
