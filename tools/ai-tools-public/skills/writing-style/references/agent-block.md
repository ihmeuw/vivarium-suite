# Agent prose block

A subagent cannot load a skill by itself, so an agent whose prose reaches people gets
the line below in its constraints. Copy the line exactly. `scripts/check_agent_blocks.py`
reports an agent file whose copy is different from this one, and a required agent that
does not have the line. Add a new agent that needs the line to `REQUIRED_AGENTS` in that
script.

Agents whose output only the orchestrator reads do not need the line.

<!-- agent-block:begin -->
- Write prose for people in plain technical English. Use short sentences in the active voice and simple tenses, and one name for each thing. Do not use semicolons, Latin abbreviations, or metaphors. Put code identifiers and paths in backticks.
<!-- agent-block:end -->
