---
name: workflow-assessment
description: Assess a finished (or in-progress) run of an agentic workflow — did the multi-agent handoffs and tool invocations happen the way the workflow's definition says they should? Reads the Claude Code session transcripts, fans out the `_trace_extractor` sub-agent, and judges the trace against the workflow definition across coverage, ordering/gates, parallelism, handoff completeness, tool appropriateness, and result propagation. Trigger on "assess that run", "check the handoffs", "did the workflow orchestrate correctly", "audit the last code-review run", "workflow assessment".
allowed-tools: Read, Grep, Glob, Agent(_trace_extractor)
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

## Resolving the run

The session to assess is rarely named by id — the user describes it: roughly
*when* it ran and *what they were doing* ("the repo-maintenance run on
vivarium-suite last Thursday", "yesterday's code review of the config-tree
PR"). Resolve that description to one session by cross-referencing cheap
metadata — **never by reading transcripts to search through them**:

1. **Get a description** if the user hasn't given one — roughly when, and
   which workflow / repo / task. Two shortcuts skip straight to the fan-out:
   the user means the **current session** (note in the report that its
   transcript is still being written and includes this assessment), or they
   hand you an explicit session id or transcript path.
2. **Find the candidate project dirs.** Sessions live under
   `~/.claude/projects/<project-slug>/`, where the slug is the run's working
   directory with punctuation flattened to `-` (honor `$CLAUDE_CONFIG_DIR`,
   which relocates the `~/.claude` base). One repo worked from several
   worktrees has several sibling slugs, so Glob `*<repo>*` to catch the main
   checkout and every worktree at once — "on vivarium-suite" must not miss a
   worktree run.
3. **Fingerprint each session without opening its transcript.** Per `<id>`:
   - `<id>.jsonl`'s **mtime** is when it ran (Glob orders results by mtime);
   - the **`<id>/subagents/agent-*.meta.json`** sidecars are tiny — read them
     inline; their `agentType`s and dispatch descriptions identify the
     workflow (`_claim_auditor` ⟹ repo-maintenance; the five `_review_*` ⟹
     code-reviewer; `_feature_implementer` + `_test_writer` ⟹
     framework-development; …). No `subagents/` folder ⟹ no sub-agents ran.
   - only if the fingerprint is still ambiguous, Grep the file's opening
     `<command-name>` tag for the exact slash command — a bounded peek at the
     head, not a transcript read.
4. **Confirm before fanning out.** Rank candidates by time + fingerprint match
   and name your pick back in terms the user can verify — its time and shape,
   not its opaque id ("the 06-18 run that fanned out 33 `_claim_auditor`s") —
   or show a short table if several are close. The id means nothing to the
   user; let them recognize the run by its description.

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

When the run is ad-hoc usage with no workflow definition to judge against,
grade only the spec-free dimensions — gates (2), tool appropriateness against
each agent's own declared role (5), and result propagation (6) — mark
coverage, ordering, parallelism, and handoff completeness `N/A`, and say so in
the report.

## Extraction fan-out

Enumerate the sub-agent transcripts yourself (Glob the `subagents/*.meta.json`
files — they're tiny; read them inline). Then dispatch `_trace_extractor`
**in a single message**: one for the main transcript plus one per sub-agent
transcript. Each brief carries the absolute transcript path, the role hint
from its `meta.json`, and any spec-driven focus (e.g. "report whether the
prompt contains an absolute worktree path"); the main-transcript brief also
carries the `subagents/` directory path so dispatch outcomes can be
cross-checked. The extractor owns transcript mechanics; hand it paths and
questions, not parsing instructions.

## Assessment dimensions

Judge the assembled traces against the contract, dimension by dimension:

1. **Coverage** — every mandated sub-agent and phase actually ran, in the
   mandated multiplicity (five `_review_*` lenses means five dispatches).
   Mandated steps with no trace evidence are FAILs, not gaps.
2. **Ordering & gates** — phases ran in the prescribed order; every gated
   action has a user answer *before* it, per the extractor's Gates digest;
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
evidence (`transcript:line`, quote ≤1 line) or states why the evidence is
unavailable. The verdict derives mechanically from the grades: any FAIL →
`DEVIATIONS FOUND`; else any WARN → `SOUND WITH WARNINGS`; else `SOUND`.

```
## Workflow assessment: <workflow> — session <short-id>

### Verdict
<one line: SOUND | SOUND WITH WARNINGS | DEVIATIONS FOUND, plus the headline reason>

### Dimensions
| Dimension | Grade | Note |
<one row each: PASS / WARN / FAIL / N/A — one-line note>

### Findings
<numbered; each: grade, evidence reference, what the definition says, what the run did>

### Recommendations
<two lists: (a) workflow-definition fixes — when the definition itself caused or permitted
the deviation; (b) run hygiene — one-off issues with this execution. Skip an empty list.>
```

