---
name: type-hinter
description: "Type-hint a target — a whole package, a sub-folder, or individual files — until it conforms to the package's mypy config. Runs as the lead of an agent team: resolves the inter-file dependency graph, spawns one autonomous teammate per file, and lets teammates negotiate shared type contracts directly. Requires Claude Code agent teams (experimental)."
argument-hint: "What to type-hint: a package or sub-folder path, a glob, or individual .py files — all within one package root."
allowed-tools: Read, Edit, Grep, Glob, Bash, Agent(viv-public:_type_hint_file)
---

Type-hint the target described in: $ARGUMENTS

You are the **lead of an agent team**. Each target file is owned by one
autonomous teammate (`viv-public:_type_hint_file`) that runs in its own
context, runs mypy itself, and resolves its own file. Teammates that share
a type contract coordinate **directly with each other** through the team
mailbox rather than routing every cross-file decision through you. Your
job: bootstrap shared config, work out the dependency graph so each
teammate knows whom to talk to, do the final verification, and own
everything the user must sign off on.

## Step 0 — Preflight the team feature

Agent teams are experimental and opt-in. Confirm that agent subteams are configured via tool availability.
There is no subagent fallback, so don't emulate the
team with one-shot sub-agents. If teams aren't available, ask the user
to turn on Claude Code's experimental agent-teams setting in their
Claude Code settings and re-run once it's enabled.

## Step 1 — Resolve the target

Interpret `$ARGUMENTS` as a description of what to type-hint — a whole
package or directory, a glob, or one or more individual `.py` files.
Expand it into the concrete **target set** of `.py` files:

- a directory or package → every `.py` under it, recursively;
- a glob → its matches;
- explicit paths → those files.

Verify the set is non-empty and every member is an existing `.py` file.
Test files (`tests/**`) are in scope — hint them like any other file.

All resolved files must resolve under a single package root — the
directory where its `pyproject.toml` / mypy config lives. Set `PKG_ROOT`
to that directory.

## Step 2 — Probe the package

Read `${PKG_ROOT}/pyproject.toml`. If it lacks a `[tool.mypy]` block with
`mypy_path = "src"` and `explicit_package_bases = true`, add it, starting
from this template (adjust `mypy_path` if the sources don't live under
`src/`):

```toml
[tool.mypy]
mypy_path = "src"
explicit_package_bases = true
strict = true
```

Announce the diff and proceed — don't
gate on it; the user wants the skill to bootstrap missing config. The
block must exist before teammates spawn, since each teammate's mypy runs
read it.

## Step 3 — Capture the baseline

Run the package's mypy invocation — `make mypy`, `mypy src/`, or
whatever the repo defines — from `${PKG_ROOT}` once and record the **set** of errors
in **non-target** files — each error's file, line, and code, not just the
count. That set is pre-existing noise the workflow must not add to. You
check against it in step 8: incidentally fixing a baseline error is fine,
but introducing a *new* non-target error is a regression even if the total
count holds or drops.

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

Spawn one `viv-public:_type_hint_file` teammate per target file as a **team**
(persistent and able to message each other — not one-shot sub-agents).
Brief each with: `file`; `package` (`${PKG_ROOT}`); `target_files` (the
set plus each teammate's name); `upstream` (files/symbols it consumes and
the owning teammate); `downstream` (consumers and the symbols they take);
`owned_symbols`; and a `baseline` note that it iterates with mypy scoped
to its own file (`mypy <file>` from `${PKG_ROOT}`) — the whole-package
mypy baseline and final gate are yours, not theirs.

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
unresolved items with the user, in order. **Keep the team alive through
this step** — accepted fixes route back to the owning teammate, who holds
the file's context. Tear the team down only once every accepted change is
in; then run step 8 yourself.

1. **External-package overrides**: present each proposed
   `[[tool.mypy.overrides]]` with its mypy error — the usual shape:

   ```toml
   [[tool.mypy.overrides]]
   module = ["<package>", "<package>.*"]
   ignore_missing_imports = true
   ```

   Apply only with sign-off, to
   `${PKG_ROOT}/pyproject.toml` — **you** own this file; teammates never
   edit it.
2. **Logic-change candidates**: present each with file:line, the current
   code, and what mypy expects. Never apply unilaterally. If the user
   declines, offer a `# type: ignore` (code + reason) or to leave the
   error. Route an accepted fix back to the owning teammate. Only apply it
   yourself if the team is already torn down.
3. **`# type: ignore` candidates**: present each (file:line, code,
   justification); apply only the accepted ones.

## Step 8 — Final verification and `py.typed`

Run the package's whole-package mypy invocation a final time — plus the
repo's broader check command, if it defines one. Confirm
zero errors in target files and that the non-target errors are a **subset**
of the step-3 baseline — no error you didn't start with, even if the total
count dropped. If either fails, return to step 7 (or re-engage the relevant
teammate). Don't proceed until this passes, or until the only remaining
errors are ones the user explicitly accepted.

Then decide `py.typed`: **if and only if this final run reports zero
errors for the whole package** (not just the target files — accepted ones
left on the floor count as errors), the package is now fully typed, so add
the marker at `${PKG_ROOT}/src/.../py.typed`, alongside the package's
top-level `__init__.py`.
If any error remains, leave `py.typed` absent — the package isn't fully
typed and this run is a partial conversion. If your CI keys mypy
enforcement off the marker, it must be honest.

Adding `py.typed` is a two-part change: setuptools doesn't ship
non-Python files in the wheel, so the marker also needs a
`[tool.setuptools.package-data]` entry in `${PKG_ROOT}/pyproject.toml`.
Add it keyed on the package's own import path, comment included:

```toml
[tool.setuptools.package-data]
# Ship the py.typed marker (PEP 561) so mypy treats this package as typed when
# users install it from PyPI and so mypy also finds the marker via the
# editable install during in-repo type checks.
"<package.import.path>" = ["py.typed"]
```

Never add one without the other — a marker without the entry silently
leaves installed copies of the package untyped, and the entry without
the marker is dead config.

## Step 9 — Report

Leave all changes in the working tree — don't stage or commit. Print:

- Files touched, hint count, and owning teammate.
- The step-4 graph and the contracts settled (symbol → owner → consumers →
  agreed type).
- mypy delta (baseline → final).
- Items the user accepted (declined logic changes, accepted ignores,
  applied overrides).
- Whether step 2 bootstrapped `[tool.mypy]`, and whether step 8 added
  `py.typed` and its `[tool.setuptools.package-data]` entry (if not,
  note the package is still a partial conversion).

Then point the user at `/viv-public:commit-splitter` to dole the diff into
reviewable commits.
