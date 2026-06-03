=================
Vivarium AI Tools
=================

Vivarium AI Tools is a Claude Code plugin providing custom agent
workflows for vivarium development. The plugin lives under
``tools/ai-tools/`` in the ``vivarium-suite`` monorepo, and is
published through a single-plugin marketplace whose catalog
(``.claude-plugin/marketplace.json``) lives at the monorepo root, so
Claude Code users can install it via
``/plugin marketplace add ihmeuw/vivarium-suite``.

It includes:

**Code Reviewer**

- ``code_reviewer`` — orchestrator that delegates to a number of specialist sub-agents focused on:
  
  - Maintainability
  - DRY
  - Structural design choices
  - Testing coverage and quality
  - Documentation

Slash command (Claude Code only): ``/viv:code-review <PR or description>``.

**Regression Debugger**

- ``model_regression_debugger`` — orchestrator that traces data pipeline changes across repos to find the cause of simulation regressions

Slash command (Claude Code only): ``/viv:debug-regression <symptom and context>``.

**Git Rescue**

- Slash command (Claude Code only): ``/viv:git-rescue [optional description]``.
  Diagnoses and untangles messy git situations — stuck interactive
  rebases, stacked-branch conflicts after a squash-merge of the parent,
  divergent history, accidental merge commits, dropped commits. Always
  creates a backup ref before rewriting history and gates every
  destructive step (including the final ``git push --force-with-lease``)
  on explicit user confirmation. User-invoked only — there is no
  auto-trigger.

**Skills**

- ``plugin-setup`` — walks the user through post-install configuration that the
  plugin install itself doesn't perform.
- ``continuous-integration`` — catalogues the vivarium-suite CI setup.
- ``team-conventions`` — SimSci Engineering conventions for everyday change
  flow.
- ``pytest`` — reference for the vivarium pytest setup: ``make test-*`` entry
  points, the ``slow`` / ``cluster`` / ``weekly`` markers from
  ``vivarium_testing_utils``, and where baked-in coverage output lands.
- ``framework-clis`` — reference for the vivarium-ecosystem console scripts
  (``simulate``, ``psimulate``, ``vipin``, per-repo ``make_artifacts``,
  ``update_gbd_round``) available in a standard model-repo env.
- ``environments`` — discovery-first guidance for picking up the right Python
  environment in a vivarium repo.
- ``vivarium-research`` — connector for the Vivarium Research
  documentation (https://vivarium-research.readthedocs.io). Discovers
  the docs nav tree on demand and searches modelling-strategy content
  via the Read the Docs v2 API.
- ``design-doc`` — SimSci Engineering convention for drafting a design
  document on the IHME hub
- ``brainstorming`` — structured design exploration that produces a Jira
  plan comment, a new Jira ticket, or a Confluence design doc; ships a
  browser-based Mermaid diagramming companion
- ``commit-splitter`` — dole out a bulk uncommitted diff into reviewable
  commits, and PR-sized branches when scope warrants.

Loaded automatically when the context is relevant to the skill's description.
Layout
======

The marketplace catalog lives at the monorepo root; the plugin itself lives under
``tools/ai-tools/``:

- ``<repo-root>/.claude-plugin/marketplace.json``: marketplace catalog. Its single
  plugin entry uses ``"source": "./tools/ai-tools"`` to point at the plugin
  directory. Claude Code requires the marketplace catalog at the repo root for
  ``/plugin marketplace add ihmeuw/vivarium-suite`` to find it.
- ``tools/ai-tools/.claude-plugin/plugin.json``: plugin manifest (also auto-detected
  by VS Code Copilot).
- ``tools/ai-tools/agents/``: orchestrator agents (Copilot entry points) and
  specialist sub-agents.
- ``tools/ai-tools/commands/``: Claude Code slash commands.
- ``tools/ai-tools/skills/``: Claude Code skills (model-loaded reference material for setup and usage flows)
- ``tools/ai-tools/CHANGELOG.rst``: history of plugin changes.

Top-level project metadata (license, code of conduct, contributing guide) lives at the
monorepo root and applies to this tool as well.

Installing in Claude Code
=========================

From GitHub:

.. code-block:: shell

   /plugin marketplace add ihmeuw/vivarium-suite
   /plugin install viv@vivarium-ai-tools

For local development against a checked-out monorepo, point ``marketplace add``
at the repo root (the directory containing ``.claude-plugin/``), not at
``tools/ai-tools/``:

.. code-block:: shell

   /plugin marketplace add /path/to/vivarium-suite
   /plugin install viv@vivarium-ai-tools

Once installed, the canonical Claude Code entry points are the slash
commands ``/viv:code-review`` and ``/viv:debug-regression``. These run
the parallel sub-agent fan-out at main-session level and produce a
multi-lens review or investigation.

The ``code_reviewer`` and ``model_regression_debugger`` agent files
exist for VS Code Copilot, which has no slash-command surface. On
Claude Code, if a user invokes them directly via ``@code_reviewer`` or
``@model_regression_debugger``, the agent's first step is to detect the
harness and output a one-line redirect telling the user to use the
slash command instead. Do not rely on the ``@`` invocation path on
Claude.

Delegation mechanism
====================

Sub-agent delegation works differently on each platform, and the plugin
uses two separate mechanisms that target the two harnesses
independently.

**Claude Code.** Sub-agents cannot spawn further sub-agents (per the
upstream `Claude Code sub-agents docs
<https://code.claude.com/docs/en/sub-agents.md>`_), so the parallel
fan-out has to run at main-session level. That is what the
``commands/*.md`` slash commands do: their ``allowed-tools: Agent(...)``
field grants the main session permission to spawn the listed
``_review_*`` (or ``_diff_analyzer`` / ``_hypothesis_tester``)
sub-agents in parallel, and the slash command body is itself the
orchestration prompt. The orchestrator agent files are *not* invoked
by the slash command — the fan-out targets the specialist sub-agents
directly.

**VS Code Copilot.** Sub-agent delegation is the orchestrator's job
and is configured via two front-matter fields on the orchestrator
agent: ``tools:`` must contain the ``agent`` token, and an
``agents: [...]`` list enumerates allowed sub-agents. Both are
declared on ``code_reviewer`` and ``model_regression_debugger``.
Copilot has no slash-command surface; the agent picker is the only
entry point.

The orchestrator agent files use only Copilot tool vocabulary
(``read, search, execute, github/*, agent``) — Claude-style PascalCase
tokens are intentionally absent, because the canonical Claude path is
the slash command and there is no scenario where the orchestrator
agent would run usefully under Claude. The ``_review_*``,
``_diff_analyzer``, and ``_hypothesis_tester`` sub-agent files do
declare both vocabularies (they are invoked from both the Claude
slash command and from Copilot's orchestrators). Do not consolidate
these vocabularies — each platform recognizes its own tokens and
silently drops the other's, and the cross-platform compatibility
relies on both being present where applicable.

Security model and recommended deny rules
=========================================

The agents in this plugin have the following shell access on Claude
Code:

- The 5 ``_review_*`` sub-agents have **no Bash access at all**. They
  are fed PR context by the slash command and analyze code with
  ``Read``, ``Grep``, and ``Glob`` only.
- ``_diff_analyzer``, ``_hypothesis_tester``, and ``_split_proposer``
  declare ``Bash`` to run ``git`` and ``gh`` commands. In practice, every
  operation they perform is a read-only git command (``git diff``,
  ``git log``, ``git show``, ``git status``), which Claude Code
  auto-approves via its built-in read-only command allowlist.
  ``_split_proposer`` is additionally constrained by its own prompt to
  never run a state-changing git command.
- The ``code_reviewer`` and ``model_regression_debugger`` orchestrator
  agents are Copilot-only and have no Claude tools — on Claude Code
  they redirect to the slash command and exit. The Bash work on the
  Claude path is performed by the slash command body itself (running
  in the main session), which similarly only invokes read-only git/gh
  commands to gather PR/repo context.

For destructive or out-of-scope commands, Claude Code's default
permission system prompts you before execution, so a prompt-injected
agent cannot silently run ``rm``, ``curl``, or similar without your
approval.

If you run with ``defaultMode: bypassPermissions`` or ``auto``, or
otherwise want an explicit deny floor that cannot be bypassed by an
errant prompt-allow, add this snippet to ``~/.claude/settings.json``:

.. code-block:: json

   {
     "permissions": {
       "deny": [
         "Bash(git push *)",
         "Bash(git reset --hard *)",
         "Bash(git rebase *)",
         "Bash(git clean -fd *)",
         "Bash(gh repo delete *)",
         "Bash(gh auth logout *)",
         "Bash(gh pr close *)"
       ]
     }
   }

Deny rules take precedence over allow rules and over hook decisions, so
these will block the listed commands in every permission mode.

Installing in VS Code GitHub Copilot
====================================

Add this path to ``chat.pluginLocations`` in settings:

.. code-block:: json

   {
     "chat.pluginLocations": {
       "/your/path/to/vivarium-suite/tools/ai-tools": true
     }
   }

Then reload VS Code and verify the plugin appears in the Agent Plugins
UI. The agents will appear in the Copilot agent picker. Slash commands
are intentionally Claude-only — Copilot's agent picker is the
equivalent surface there.
