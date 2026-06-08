**0.13.0 - 06/08/26**

 - Add ``/viv:type-hinter`` slash command and ``_type_hint_file`` teammate for type-hinting a target (a package, sub-folder, or individual files) under one package until ``make mypy`` passes. The command runs as the lead of an agent team: it resolves the inter-file dependency graph, spawns one autonomous teammate per file, and the teammates coordinate shared type contracts directly via the team mailbox. Adds ``py.typed`` only if the whole package ends mypy-clean, and hands the resulting diff to ``/viv:commit-splitter``. Requires Claude Code agent teams (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``, v2.1.32+)

**0.12.1 - 06/03/26**

 - Add `make check` guidance to CLAUDE.md

**0.12.0 - 06/03/26**

 - Add ``commit-splitter`` skill and ``_split_proposer`` specialist sub-agent for doling out a bulk uncommitted diff into reviewable commits and PR-sized branches
 
**0.11.1 - 06/01/26**

 - Update ``git-rescue`` skill to run `make check` and summarize changes before pushing

**0.11.0 - 06/01/26**

 - Add ``git-rescue`` slash command for diagnosing and untangling messy git histories (stuck rebases, stacked-branch squash-merge conflicts, divergent history) with mandatory backup refs and per-step confirmation

**0.10.0 - 05/28/26**

 - Add ``brainstorming`` skill for structured design exploration that produces a Jira plan comment, a new Jira ticket, or a Confluence design doc; ships a browser-based Mermaid diagramming companion
 - Update ``plugin-setup`` skill with a section covering Node.js install for the brainstorming visual companion

**0.9.1 - 05/28/26**

 - Update ``team-conventions`` skill to use the Jira MCP for ticket creation now that it has write access

**0.9.0 - 05/27/26**

 - Add ``environments`` skill covering env discovery and creation across vivarium repos
 
**0.8.0 - 05/27/26**

 - Add ``vivarium-research`` skill for searching the Vivarium Research documentation via the Read the Docs API
 
**0.7.0 - 05/26/26**

 - Add ``design-doc`` skill for drafting a design document on the IHME hub

**0.6.0 - 05/20/26**

 - Add ``pytest`` skill covering vivarium pytest conventions, markers, and scope expansion
 - Add ``framework-clis`` skill covering vivarium console scripts on PATH in a model-repo env

**0.5.1 - 05/19/26**

- Add LICENSE file
- Remove .gitignore file

**0.5.0 - 05/18/26**

 - Add ``team-conventions`` skill covering SimSci Engineering conventions
 - Add dependency for anthropic slack plugin
 - Add make command skill

**0.4.0 - 05/13/26**

 - Add ``continuous-integration`` and ``plugin-setup`` skills covering install and interaction
   with the SimSci Jenkins MCP server.

**0.3.2- 05/13/26**

- Put the marketplace at the repo root
- Add dependency for anthropic skill-creator

**0.3.1 - 05/13/26**

- Fix marketplace directory path for monorepo

**0.3.0 - 05/12/26**

 - Migrate from standalone ``ihmeuw/vivarium_ai_tools`` repo into the
   ``vivarium-suite`` monorepo under ``tools/ai-tools/``. Plugin marketplace
   install path has changed; see README for new instructions.

**0.2.0**

 - Restructure as a Claude Code plugin with a self-hosted marketplace.
 - Better restrict tool invocation frontmatter
 - Restructure sub-agent delegation to match claude and copilot-specific architecture

**0.1.0 - 7/29/25**

 - Initial repository setup
