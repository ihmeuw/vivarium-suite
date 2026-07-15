---
name: workflow-assessment
description: Assess a finished run of an agentic workflow — did the multi-agent handoffs and tool invocations happen the way the workflow's definition says they should? Reads the Claude Code session transcripts and judges the trace against the workflow definition across coverage, ordering/gates, parallelism, handoff completeness, tool appropriateness, and result propagation. Trigger whenever the user wants to debug or verify the procedural correctness of a Claude session, especially ones with engineered orchestration or multi-agentic workflows.
allowed-tools: Read, Grep, Glob, Agent(_trace_extractor)
---

# Workflow assessment

Verify a workflow run orchestrated the way its definition prescribes — right
sub-agents, dispatched the right way, complete handoffs, gates respected,
results used — and report graded findings with transcript evidence.

## Resolving the run

The user describes the session, not its id. Resolve it from cheap metadata —
**never by reading transcripts to search**:

1. **Get a description** if missing — when, and which workflow / repo / task.
   Shortcuts: the **current session**, or an explicit id / transcript path.
2. **Find candidate dirs.** Sessions live under
   `~/.claude/projects/<project-slug>/` — the working dir with punctuation
   flattened to `-` (honor `$CLAUDE_CONFIG_DIR`). Glob `*<repo>*` to catch
   worktree slugs.
3. **Fingerprint without opening transcripts.** The `.jsonl`'s **mtime** is when
   it ran; the tiny `subagents/agent-*.meta.json` sidecars name the workflow by
   their `agentType`s (e.g. `_claim_auditor` ⟹ repo-maintenance). No `subagents/`
   ⟹ no sub-agents ran. Still ambiguous? Grep the opening `<command-name>` tag.
4. **Confirm** by naming your pick back by time and shape, not its id; a short
   table if several match.

## Loading the spec

The definition is the contract — the slash-command (`commands/<name>.md`) or
skill (`skills/<name>/SKILL.md`). Distill only what it *mandates*:

- which sub-agents run, and how many of each;
- what's dispatched **in parallel** ("in a single message") vs. sequenced;
- phase ordering, iteration bounds, and **user gates**;
- what each brief must contain and must **not** (isolation rules);
- which tools each participant may use.

Ad-hoc usage with no definition: grade only gates (2), tool appropriateness (5),
and result propagation (6); mark the rest `N/A`.

## Extraction fan-out

Enumerate the sub-agent transcripts yourself (Glob `subagents/*.meta.json`). Then
dispatch `_trace_extractor` **in a single message** — one per transcript (main +
each sub-agent). Each brief carries the absolute path, the role hint from its
`meta.json`, and any spec-driven focus; the main-transcript brief also carries
the `subagents/` directory for cross-checking. Hand it paths and questions, not
parsing instructions.

## Assessment dimensions

1. **Coverage** — every mandated sub-agent and phase ran, in the mandated
   multiplicity. No trace evidence = FAIL, not something you can ignore or rationalize away.
2. **Ordering & gates** — phases ran in order; every gated action has a user
   answer *before* it; bounded loops stayed bounded.
3. **Parallelism** — dispatches the definition parallelizes share one batch;
   dependent steps don't.
4. **Handoff completeness** — each brief contains what's mandated and omits
   what's forbidden; check isolation both ways.
5. **Tool appropriateness** — each participant stayed in role; the main session
   didn't inline a sub-agent's job — one stray Grep is hygiene, doing its whole
   job isn't.
6. **Result propagation** — each returned digest is visibly used downstream. A
   result nothing consumes is a silent drop.

## Output format

Omit empty sections. Every WARN/FAIL cites evidence (`transcript:line`, ≤1-line
quote) or says why it's unavailable. Verdict is mechanical: any FAIL →
`DEVIATIONS FOUND`; else any WARN → `SOUND WITH WARNINGS`; else `SOUND`.

```
## Workflow assessment: <workflow> — session <short-id>

### Verdict
<verdict — headline reason>

### Dimensions
| Dimension | Grade | Note |   (one row each: PASS / WARN / FAIL / N/A)

### Findings
<numbered: grade, evidence ref, definition-says vs. run-did>

### Recommendations
<(a) definition fixes — when the definition caused the deviation; (b) run hygiene.
Skip an empty list.>
```
