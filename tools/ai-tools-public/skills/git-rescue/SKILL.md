---
name: git-rescue
description: "Diagnose and untangle a messy git situation (stuck rebases, stacked-branch conflicts after squash-merge, divergent history, dropped commits)."
argument-hint: "Optional: short description of what's wrong. If omitted, inspect the current branch and figure it out."
allowed-tools: Read, Edit, Write, Bash
---

The user invoked this because their git history is in a state they don't
know how to untangle: $ARGUMENTS

## What to do

1. Inspect the repo (status, log, reflog, in-progress rebase/merge
   state, upstream divergence) and diagnose what's going on. If the
   diagnosis is ambiguous, name the two most likely scenarios and ask
   one targeted question.
2. Before the first history-rewriting command, create a backup ref:
   `git branch backup/<branch>-$(date +%Y%m%d-%H%M%S)`. Tell the user
   its name.
3. State the plan in plain English with the exact commands. Wait for
   confirmation.
4. Execute. Resolve conflicts file-by-file with Read/Edit, narrating
   each choice.
5. Verify with `git log --oneline -10` and, if an upstream is set,
   `git diff @{u}...HEAD --stat`.
6. Run the repo's standard verification command directly in Bash if
   one exists (e.g. `make check`, `make test`, or the project's CI
   script) — just execute it; no skill lookup is needed for this step.
7. Summarize the changes for the user.
8. Ask before pushing. On explicit OK, `git push --force-with-lease`.

## Non-obvious diagnostic worth knowing

**Stacked branch where the parent was squash-merged.** User branched B
off A, A was squash-merged to main, plain `git rebase main` now produces
phantom conflicts on every commit that was in A. Fix:
`git rebase --onto main <A-tip-sha> B`. Finding the right `<A-tip-sha>`
is the hard part — check `git reflog show A` if A still exists locally,
or read `git log B` to find where B's own work starts. Don't use
`git merge-base B main^` here; it's often wrong.

## Hard rules

- Confirm before every destructive step, not just the first one.
- Never `git push --force` plain — always `--force-with-lease`.
- Refuse to force-push `main` or `master`.
- Never `--no-verify` / `--no-gpg-sign` unless the user explicitly asks.
- Never run plain `git rebase -i` — it opens an editor this session
  cannot drive. Use `--onto`, cherry-pick, or `--autosquash` instead,
  or hand the user the command to run in their own terminal.
