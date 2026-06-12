---
name: workflow-assessment
description: Assess a finished (or in-progress) run of an agentic workflow — did the multi-agent handoffs and tool invocations happen the way the workflow's definition says they should? Reads the Claude Code session transcripts, fans out the `_trace_extractor` sub-agent, and judges the trace against the workflow definition across coverage, ordering/gates, parallelism, handoff completeness, tool appropriateness, and result propagation. Trigger on "assess that run", "check the handoffs", "did the workflow orchestrate correctly", "audit the last code-review run", "workflow assessment".
---

# Workflow assessment

Verify that a run of an agentic workflow orchestrated the way its definition
prescribes, and report graded findings with transcript evidence.

## Input

<!-- stub: how the target run is named (current session, most recent, session id/path), and which workflow definition is the spec -->

## Locating the run

<!-- stub: transcript discovery under ~/.claude/projects/<project-slug>/ — main JSONL + subagents/ directory -->

## Loading the spec

<!-- stub: resolve the workflow's definition file (command/skill markdown) and distill the orchestration contract from it -->

## Extraction fan-out

<!-- stub: dispatch _trace_extractor over the main transcript and each sub-agent transcript, in parallel; what each brief contains -->

## Assessment dimensions

<!-- stub: the six checks — coverage, ordering & gates, parallelism, handoff completeness, tool appropriateness, result propagation -->

## Output format

<!-- stub: per-dimension PASS/WARN/FAIL report skeleton with evidence references and a remediation split (definition fix vs. one-off) -->

## Key disciplines

<!-- stub: read-only; evidence over vibes; degrade gracefully when no spec applies; stay out of the assessed workflow's lane -->
