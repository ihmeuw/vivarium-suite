---
name: workflow-assessment
description: Assess a finished (or in-progress) run of an agentic workflow — did the multi-agent handoffs and tool invocations happen the way the workflow's definition says they should? Reads the Claude Code session transcripts, fans out the `_trace_extractor` sub-agent, and judges the trace against the workflow definition across coverage, ordering/gates, parallelism, handoff completeness, tool appropriateness, and result propagation. Trigger on "assess that run", "check the handoffs", "did the workflow orchestrate correctly", "audit the last code-review run", "workflow assessment".
---

# Workflow assessment

Verify that a run of an agentic workflow orchestrated the way its definition
prescribes — the right sub-agents, dispatched the right way, with complete
handoffs, gates respected, and results actually used — and report graded
findings with transcript evidence.

This is a **post-hoc transcript audit**, and therefore Claude Code-only: it
reads Claude Code session transcripts, which have no Copilot equivalent. It is
read-only throughout — it never re-runs the workflow, edits its outputs, or
modifies the workflow definition.

## Input

Two things identify the job; take them from the user or resolve them yourself:

1. **The run.** "This run" / "that run" in the session that just executed the
   workflow means the **current session** (note in the report that the
   transcript is still being written, and includes this assessment
   conversation). Otherwise: the most recent transcript in the project
   directory, or an explicit session id or transcript path.
2. **The workflow.** Usually obvious from the user or the transcript (the
   `<command-name>` tag or `Skill` invocation that started it). When the
   session is just ad-hoc agent usage with no definition to judge against, run
   the generic checks only and label the report accordingly.

## Locating the run

Transcripts live under `~/.claude/projects/<project-slug>/`, where the slug is
the session's working directory with `/` replaced by `-` (e.g.
`-home-user-repos-vivarium-suite`). Per session `<id>`:

- `<id>.jsonl` — the main transcript;
- `<id>/subagents/agent-*.jsonl` — one transcript per spawned sub-agent, each
  with an `agent-*.meta.json` (`{agentType, description, toolUseId}`).

"Most recent" is mtime order. Mind two traps: a run executed from a git
worktree has a *different* cwd and therefore a different project slug (list
`~/.claude/projects/` when the obvious slug comes up empty), and a directory
with no `subagents/` folder means no sub-agents were spawned — which is itself
an assessment finding for a workflow that mandates a fan-out.

## Loading the spec

The workflow's own definition is the contract. For this plugin's workflows
that is the slash-command file (`commands/<name>.md`) or skill
(`skills/<name>/SKILL.md`), in the repo checkout or the installed plugin copy.
Read it and distill the orchestration contract — only what the definition
actually mandates, not what you'd have designed:

- which sub-agents must run, and how many of each;
- what must be dispatched **in parallel** ("in a single message") vs. sequenced;
- phase ordering, iteration bounds, and **user gates** (steps that require
  explicit approval before proceeding);
- what each dispatch brief must contain (paths, design context, diffs) and
  what it must **not** contain (isolation rules, e.g. black-box TDD's "the
  implementer never sees filled-in test bodies");
- which tools each participant is supposed to use (who writes, who only
  reads, what stays out of the main session).

## Extraction fan-out

Enumerate the sub-agent transcripts yourself (Glob the `subagents/*.meta.json`
files — they're tiny; read them inline). Then dispatch `_trace_extractor`
**in a single message**: one for the main transcript plus one per sub-agent
transcript. Each brief carries the absolute transcript path, the role hint
from its `meta.json`, and any spec-driven focus (e.g. "report whether the
prompt contains an absolute worktree path"). The extractor owns transcript
mechanics; hand it paths and questions, not parsing instructions.

For very large runs (more than ~12 sub-agent transcripts), extract the main
transcript plus the sub-agents the spec has expectations about, and name the
ones you skipped in the report — never cap silently.

## Assessment dimensions

Judge the assembled traces against the contract, dimension by dimension:

1. **Coverage** — every mandated sub-agent and phase actually ran, in the
   mandated multiplicity (five `_review_*` lenses means five dispatches).
   Mandated steps with no trace evidence are FAILs, not gaps.
2. **Ordering & gates** — phases ran in the prescribed order; every gated
   action has a user answer *before* it in the transcript (an
   `AskUserQuestion` or a turn-ending question answered by the user);
   bounded loops stayed within their bound.
3. **Parallelism** — dispatches the definition says to parallelize share one
   batch; genuinely dependent steps don't.
4. **Handoff completeness** — each dispatch brief contains what the
   definition mandates and omits what it forbids. Check isolation rules both
   ways: required context present, forbidden context absent.
5. **Tool appropriateness** — each participant operated within its documented
   role: read-only agents didn't write, writers didn't run the suite, and the
   main session didn't inline work the definition delegates to a sub-agent
   (one stray Grep is hygiene; doing a sub-agent's whole job is a deviation).
6. **Result propagation** — each sub-agent's returned digest is visibly used
   downstream (findings triaged, verdicts acted on, escalations answered).
   A result that nothing consumes is a silent drop.

## Output format

Omit any section with nothing to say. Every WARN/FAIL cites transcript
evidence (`transcript:line`, quote ≤1 line).

```
## Workflow assessment: <workflow> — session <short-id>

### Verdict
<one line: SOUND | SOUND WITH WARNINGS | DEVIATIONS FOUND, plus the headline reason>

### Dimensions
| Dimension | Grade | Note |
<one row each: PASS / WARN / FAIL / N-A — one-line note>

### Findings
<numbered; each: grade, evidence reference, what the definition says, what the run did>

### Recommendations
<two lists: (a) workflow-definition fixes — when the definition itself caused or permitted
the deviation; (b) run hygiene — one-off issues with this execution. Skip an empty list.>
```

## Key disciplines

- **The definition is the contract.** Judge the run against what its
  definition mandates, not against taste; if the run deviated *because the
  definition is ambiguous or wrong*, say so and put the fix under
  workflow-definition recommendations.
- **Evidence over inference.** No FAIL without a transcript citation. If the
  transcript can't show it (truncated record, missing file), grade WARN and
  say why.
- **Degrade loudly.** No definition, missing transcripts, skipped extractors,
  or a still-running session — all stated in the report, never papered over.
- **Stay read-only.** Assessment changes nothing: no edits to definitions,
  transcripts, or the assessed run's outputs. Recommendations are advisory;
  leftover definition fixes can flow into `ticket-triage` if the user wants
  tickets.
- **Compact context.** Raw transcript bulk stays in the extractors; if a
  digest comes back oversized or judgmental, tighten the brief and re-dispatch
  rather than absorbing it.
