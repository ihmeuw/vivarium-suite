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

  The orchestrator also runs its own functional-correctness pass.

Slash command (Claude Code only): ``/viv:code-reviewer <PR or description>``.
After the review, findings the user won't address in the current PR can be
handed to the ``ticket-triage`` skill (see Skills below), to compile and file non-duplicate JIRA tickets.

**Regression Debugger**

- ``model_regression_debugger`` — orchestrator that traces data pipeline changes across repos to find the cause of simulation regressions

Slash command (Claude Code only): ``/viv:model-regression-debugger <symptom and context>``.

**Git Rescue**

- Slash command (Claude Code only): ``/viv:git-rescue [optional description]``.
  Diagnoses and untangles messy git situations — stuck interactive
  rebases, stacked-branch conflicts after a squash-merge of the parent,
  divergent history, accidental merge commits, dropped commits. Always
  creates a backup ref before rewriting history and gates every
  destructive step (including the final ``git push --force-with-lease``)
  on explicit user confirmation. User-invoked only — there is no
  auto-trigger.

**Type Hinter**

- Slash command (Claude Code only): ``/viv:type-hinter <target>`` (a
  package, sub-folder, or ``.py`` files under one ``libs/<pkg>/``). Runs
  as the **lead of an agent team**: resolves the inter-file dependency
  graph, spawns one teammate per file, verifies with ``make mypy``, and
  adds ``py.typed`` only if the package ends clean. **Requires agent
  teams** (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``, v2.1.32+; no
  fallback). It **writes**, then hands the diff to
  ``/viv:commit-splitter``.

**Framework Development**

- ``framework_developer`` — orchestrator for an end-to-end
  design → implement → verify → PR loop on a single well-scoped framework
  feature. The main session owns the design and the interface stubs, then runs
  a **black-box TDD** build. The orchestrator owns the contract: it writes
  **source stubs** (the API) plus **body-less test stubs** that enumerate the
  acceptance criteria, commits them, and creates two git worktrees from that
  baseline. It then fans out ``_feature_implementer`` and ``_test_writer`` in
  parallel — the tester fleshes out the test stubs, the implementer fills the
  source bodies treating those stubs as read-only criteria — each confined to its
  own worktree, whose lineages never merge, so neither ever sees the other's
  filled-in code (the implementer gets the criteria but not the assertions). It
  then integrates the two lineages and fans out a ``_validator`` for the
  test/lint/type suite and runs the shared ``_review-core`` skill for review,
  iterating while preserving the black box. It always creates the feature branch
  up front and gates PR creation on explicit user approval.

Slash command (Claude Code only): ``/viv:framework-development <ticket or feature description>``.

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
- ``ticket-triage`` — turn code-review findings that are out of scope for
  the current PR into Jira ticket recommendations.
- ``workflow-assessment`` — post-hoc audit of an agentic workflow run
  against the workflow's own definition: fans out the ``_trace_extractor``
  sub-agent over the run's session transcripts and grades coverage,
  ordering/gates, parallelism, handoff completeness, tool appropriateness,
  and result propagation, every WARN/FAIL backed by transcript evidence.
  Claude Code-only (it reads Claude Code session transcripts, which have
  no Copilot equivalent) and read-only throughout.

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
commands ``/viv:code-reviewer``, ``/viv:model-regression-debugger``, and
``/viv:framework-development``. These run the sub-agent fan-out at
main-session level and produce a multi-lens review, a regression
investigation, or an end-to-end feature build.

The ``code_reviewer``, ``model_regression_debugger``, and
``framework_developer`` agent files exist for VS Code Copilot, which has
no slash-command surface. On Claude Code, if a user invokes them directly
via ``@code_reviewer``, ``@model_regression_debugger``, or
``@framework_developer``, the agent's first step is to detect the harness
and output a one-line redirect telling the user to use the slash command
instead. Do not rely on the ``@`` invocation path on Claude.

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

The multi-lens review fan-out is defined once, in the internal ``_review-core``
skill (``skills/_review-core/SKILL.md``, hidden from the ``/`` menu via
``user-invocable: false``), and invoked **inline** by ``/viv:code-reviewer``
after it gathers PR context. A skill invoked from a command runs inline in the
same main session — not as a sub-agent — so ``_review-core`` can spawn the
``_review_*`` fan-out itself, keeping it one level deep. That is what lets the
review be reused by other main-session commands without duplicating the
fan-out.

**VS Code Copilot.** Sub-agent delegation is the orchestrator's job
and is configured via two front-matter fields on the orchestrator
agent: ``tools:`` must contain the ``agent`` token, and an
``agents: [...]`` list enumerates allowed sub-agents. Both are
declared on ``code_reviewer``, ``model_regression_debugger``, and
``framework_developer``. Copilot has no slash-command surface; the agent
picker is the only entry point.

The orchestrator agent files use only Copilot tool vocabulary
(``read, search, execute, github/*, agent``) — Claude-style PascalCase
tokens are intentionally absent, because the canonical Claude path is
the slash command and there is no scenario where the orchestrator
agent would run usefully under Claude. The non-user-facing sub-agent
files (everything ``_``-prefixed) do declare both vocabularies (they
are invoked from both the Claude slash commands and from Copilot's
orchestrators). Do not consolidate
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
- ``_duplicate_finder`` has **no shell or file access at all** — its only
  tools are the read-only Jira MCP ``search`` and ``get_issue`` calls it
  uses to check candidate tickets against the backlog.
- ``_trace_extractor`` has **no Bash access** — ``Read``, ``Grep``, and
  ``Glob`` only. It is the one agent that deliberately reads *outside* the
  working tree: Claude Code session transcripts under
  ``~/.claude/projects/``, which can contain anything from past
  conversations. It returns compact orchestration digests, not transcript
  content, and is spawned only by the ``workflow-assessment`` skill.
- ``_diff_analyzer``, ``_hypothesis_tester``, and ``_split_proposer``
  declare ``Bash`` to run ``git`` and ``gh`` commands. In practice, every
  operation they perform is a read-only git command (``git diff``,
  ``git log``, ``git show``, ``git status``), which Claude Code
  auto-approves via its built-in read-only command allowlist.
  ``_split_proposer`` is additionally constrained by its own prompt to
  never run a state-changing git command.
- ``_test_writer`` and ``_feature_implementer`` are the plugin's first
  **write-capable** sub-agents: they declare ``Write``/``Edit`` so they can
  author test files and fill in source stub bodies during the black-box TDD
  build. They are deliberately **not** granted ``Bash`` — they never run the
  suite, ``git``, or shell commands, which confines their effect to file edits.
  Each runs inside its own git worktree (the orchestrator does the ``git
  worktree`` management on the Claude path), so neither tree contains the
  other's output; the "stay in your worktree" instruction in each brief
  discourages reaching across via an absolute path, since the worktree is not a
  hard read sandbox. Both are spawned only by the ``/viv:framework-development``
  slash command.
- ``_validator`` declares ``Bash`` so it can run the package's ``make
  test-*`` / ``make lint`` / ``make mypy`` targets and report a PASS/FAIL
  verdict. It is read-only with respect to source and tests — it never edits
  files — but running a test suite executes arbitrary project code, so this is a
  broader grant than the read-only git agents above. It is spawned only by the
  ``/viv:framework-development`` slash command.
- The ``code_reviewer``, ``model_regression_debugger``, and
  ``framework_developer`` orchestrator agents are Copilot-only and have no
  Claude tools — on Claude Code they redirect to the slash command and exit. On
  the Claude path the slash command body (running in the main session) gathers
  PR/repo context through the GitHub MCP server (a plugin dependency; see the
  ``plugin-setup`` skill), falling back to read-only git/``gh`` commands when the
  MCP is unavailable; ``/viv:framework-development`` additionally writes source
  and test files and runs make targets as it builds the feature.

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

Recommended sandbox configuration
----------------------------------

For agentic use we recommend running Claude Code with its Bash sandbox
enabled — OS-level isolation (bubblewrap on Linux/WSL2, Seatbelt on macOS)
that confines writes to the working tree and denies reads of credential
files. The catch: a strict sandbox blocks the normal workflow unless you
grant the write paths and egress the toolchain needs. A working baseline
for ``~/.claude/settings.json``:

.. code-block:: json

   {
     "sandbox": {
       "enabled": true,
       "filesystem": {
         "allowWrite": ["~/miniconda3", "~/.conda", "~/.cache"],
         "denyRead": ["~/.ssh", "~/.aws", "~/.config/gh/hosts.yml"]
       },
       "network": {
         "allowedDomains": ["github.com", "api.github.com", "pypi.org",
                            "artifactory.ihme.washington.edu"]
       }
     }
   }

``allowWrite`` covers ``conda``/``pip``; ``denyRead`` closes the
credential-exfil path; ``network.allowedDomains`` is the egress allowlist
for sandboxed Bash. That ``denyRead`` of ``gh``'s token is why the ``gh``
CLI can't run sandboxed — hence the GitHub MCP dependency, whose calls run
outside the sandbox. Even ``git push`` runs sandboxed once ``github.com``
is allowlisted and git's credential helper points at a sandbox-readable
token file (see ``plugin-setup``), so no un-sandboxing is needed for
normal git/GitHub work.

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
