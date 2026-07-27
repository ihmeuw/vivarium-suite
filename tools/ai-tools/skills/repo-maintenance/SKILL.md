---
name: repo-maintenance
description: Audit the repo's AI plaintext — both ai-tools plugins, simsci-internal (tools/ai-tools) and simsci (tools/ai-tools-public) (skills, agents, READMEs), and CLAUDE.md — for drift against the upstream sources those docs mirror, then fix approved findings. Use when the user asks to "audit the skills", "check the AI docs for staleness", "run repo maintenance", or wonders whether the tooling docs are still accurate. Expensive (a fan-out over every unit with live upstream checks) — don't trigger it as a side effect of other work.
---

# Repo maintenance

The plugin's skills, agents, and commands — and `CLAUDE.md` — mirror
facts that live elsewhere: makefiles, workflows, Confluence pages, Slack
channels, Jenkins jobs, other repos, team conventions. Those upstreams
move; the plaintext doesn't. This skill audits every unit against its
upstreams, reports what has drifted, and applies fixes the user
approves.

The audited surface is `tools/ai-tools/**` and `tools/ai-tools-public/**`
plus the repo root `CLAUDE.md`.

## Process

1. **Enumerate.** Build the unit list: one unit per directory under
   each plugin's `skills/` (all files in the directory belong to the
   unit), one per file in each plugin's `agents/`, plus each plugin's
   `README.rst` and the repo root `CLAUDE.md`. The CHANGELOGs are
   history, not claims — skip them. Record the count; the final report must account for every unit.
2. **Fan out.** Spawn one `simsci-internal:_claim_auditor` sub-agent per unit (in
   parallel batches). Each extracts the unit's load-bearing checkable
   claims and verifies them in the same pass — the agent file owns the
   claim taxonomy, verification methods, and verdict rules; the brief
   per spawn is just the unit path and repo root.
3. **Synthesize.** Findings that share a root cause across units (the
   same stale URL cited in two skills) collapse into one finding with
   every location listed. Recompute the summary tallies from the
   per-claim `status:` fields, not the auditors' `counts:` lines (which
   can drift). Order the report `stale`, then `unverifiable`, then
   `upstream-unreachable`.
4. **Report and select.** Present the report (format below) and let the
   user pick which `stale` findings to fix — all, a subset, or none.
   The report and the selection question must be visible together at
   answer time: final message of the turn, or folded into the question
   itself — mid-turn text before a tool call may not display in
   background sessions.
5. **Fix on approval.** Apply the approved corrections — and nothing
   else. Then offer a CHANGELOG entry + version bump covering the
   fixes. Committing, PR, and release stay with the user.

## Report format

Lead with one summary line: `N units audited / M not audited — S stale,
U unverifiable, R unreachable`. Then per finding:

- the claim as written, with every `file:line` where it appears
- the upstream that was checked
- quoted upstream evidence of the mismatch (`stale` only)
- the proposed correction (`stale` only)

`unverifiable` findings (team conventions with no written source) are a
confirm-with-humans list, not failures. `upstream-unreachable` findings
name the service to reconnect ("connect the hub MCP and re-run"). Units
whose auditor died or returned garbage are listed as *not audited* —
never silently dropped.

## Constraints

- **Read-only until approval.** No file is edited before the user
  approves the specific finding; verification never invokes a
  write-capable tool.
- **Evidence or it didn't happen.** A `stale` finding arriving without
  quoted upstream evidence is dropped from the stale list and its unit
  listed as *not audited*, so it can be re-run. Synthesize from what the
  auditors returned — don't re-run their upstream checks in the
  orchestrator; thin evidence means re-run the auditor, not verify inline.
- **Don't chain.** After fixes are applied, stop. Branch setup,
  commits, and PRs are the user's call (`/simsci-internal:team-conventions` covers
  the mechanics).
