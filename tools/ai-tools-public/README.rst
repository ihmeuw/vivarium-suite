=============================================
Simulation Science Dev AI Tools (``simsci``)
=============================================

``simsci`` is a Claude Code plugin from the IHME Simulation Science team
providing generic AI-assisted developer workflows, usable by any IHME team in
any repository. It carries no SimSci- or
vivarium-specific process: every workflow runs standalone, and the places where
a team process *could* plug in (branch conventions, ticket filing, environment
setup) are optional seams — if an installed skill covers them, the workflow
follows it; otherwise it falls back to sensible generic behavior.

The plugin lives under ``tools/ai-tools-public/`` in the ``vivarium-suite``
monorepo and is published through the ``vivarium-ai-tools`` marketplace whose
catalog (``.claude-plugin/marketplace.json``) lives at the monorepo root.

It includes:

**Code Reviewer**

- ``/simsci:code-reviewer <PR or description>`` — parallel multi-agent review
  that fans out to specialist sub-agents focused on:

  - Maintainability
  - DRY
  - Structural design choices
  - Testing coverage and quality
  - Documentation

  plus its own functional-correctness pass. The five review agents run on Sonnet;
  every finding is then independently scored for confidence (0-100) by a
  per-finding ``_review_scorer`` Haiku sub-agent, and findings below 50 are
  dropped — so only verified issues reach the report, each shown with its score.

  After the review, if an installed skill covers turning leftover findings into
  tickets, the command offers to hand off to it (the SimSci team's ``simsci-internal``
  plugin provides one).

**Regression Debugger**

- ``/simsci:regression-debugger <symptom and context>`` — traces
  behavioral changes across repositories to find the cause of a regression.

**Git Rescue**

- ``/simsci:git-rescue [optional description]``.
  Diagnoses and untangles messy git situations — stuck interactive
  rebases, stacked-branch conflicts after a squash-merge of the parent,
  divergent history, accidental merge commits, dropped commits. Always
  creates a backup ref before rewriting history and gates every
  destructive step (including the final ``git push --force-with-lease``)
  on explicit user confirmation. User-invoked only — there is no
  auto-trigger.

**Type Hinter**

- ``/simsci:type-hinter <target>`` (a package, sub-folder, or ``.py``
  files under one package root). Runs as the **lead of an agent team**:
  resolves the inter-file dependency graph, spawns one teammate per file,
  verifies with the package's own mypy invocation, and adds ``py.typed``
  only if the package ends clean. **Requires agent teams**
  (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``, v2.1.32+; no fallback).
  It **writes**, then hands the diff to ``/simsci:commit-splitter``.

**Framework Development**

- ``/simsci:framework-development <ticket or feature description>`` — an
  end-to-end design → implement → verify → PR loop on a single well-scoped
  feature. The main session owns the design and the interface stubs, then runs
  a **black-box TDD** build. It owns the contract: it writes
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

**Auto-triggering skills**

- ``commit-splitter`` — dole out a bulk uncommitted diff into reviewable
  commits, and PR-sized branches when scope warrants.
- ``change-propagation`` — propagate boilerplate across several targets
  (packages in the current repository and/or external repos) in parallel, one
  ``_propagate_target`` worker per target, then converge them into one draft
  PR per repo — every durable write gated on one explicit approval.
- ``workflow-assessment`` — post-hoc audit of an agentic workflow run
  against its own definition: fans out the ``_trace_extractor`` sub-agent
  over the run's session transcripts and grades coverage, ordering/gates,
  parallelism, handoffs, tool use, and result propagation, with
  transcript-cited findings. Claude Code-only, read-only throughout.

Loaded automatically when the context is relevant to the skill's description.

Working alongside a team plugin
===============================

``simsci`` never hard-references team tooling. Where a team process could
apply, its workflows check for an installed skill that covers it (branch and PR
conventions, ticket filing, environment setup, domain reference docs) and
follow that skill when present. A team plugin can declare ``simsci`` as a
dependency — one install then brings both — and ship skills covering its
conventions; the seams resolve to those skills automatically. Installing
nothing extra works too: every workflow falls back to sensible generic
behavior.

Layout
======

The marketplace catalog lives at the monorepo root; the plugin itself lives
under ``tools/ai-tools-public/``:

- ``<repo-root>/.claude-plugin/marketplace.json``: marketplace catalog listing
  this plugin (``"source": "./tools/ai-tools-public"``) and the team's ``simsci-internal``
  plugin. Claude Code requires the marketplace catalog at the repo root for
  ``/plugin marketplace add ihmeuw/vivarium-suite`` to find it.
- ``tools/ai-tools-public/.claude-plugin/plugin.json``: plugin manifest.
- ``tools/ai-tools-public/agents/``: specialist sub-agents spawned by the
  workflow skills.
- ``tools/ai-tools-public/skills/``: Claude Code skills — both the
  user-invocable workflow entry points and internal building blocks.
- ``tools/ai-tools-public/CHANGELOG.rst``: history of plugin changes.

Installing in Claude Code
=========================

From GitHub:

.. code-block:: shell

   /plugin marketplace add ihmeuw/vivarium-suite
   /plugin install simsci@vivarium-ai-tools

For local development against a checked-out monorepo, point ``marketplace add``
at the repo root (the directory containing ``.claude-plugin/``), not at
``tools/ai-tools-public/``:

.. code-block:: shell

   /plugin marketplace add /path/to/vivarium-suite
   /plugin install simsci@vivarium-ai-tools

Once installed, the entry points are ``/simsci:code-reviewer``,
``/simsci:regression-debugger``, ``/simsci:git-rescue``,
``/simsci:type-hinter``, and ``/simsci:framework-development``, plus the
auto-triggering skills above.

The plugin's only dependency is the ``github`` plugin from the official
marketplace (installed automatically), whose GitHub MCP server the workflows
use to gather PR/repo context; they fall back to read-only ``git``/``gh``
commands when the MCP is unavailable.

.. note::
   Everything below this point is reference material — the delegation
   architecture and the security model — aimed at plugin authors and at
   reviewers vetting the plugin before an install. Day-to-day use needs
   nothing past this line.

Delegation mechanism
====================

The parallel fan-out runs at main-session level. That is what the workflow
skills do: their ``allowed-tools: Agent(...)`` field grants the main session
permission to spawn the listed ``simsci:_review_*`` (or
``simsci:_diff_analyzer`` / ``simsci:_hypothesis_tester``) sub-agents in
parallel, and the skill body is itself the orchestration prompt. Sub-agents
shipped by a plugin are namespaced at runtime — always ``simsci:<agent>``,
never the bare name.

The multi-agent review fan-out is defined once, in the internal ``_review-core``
skill (``skills/_review-core/SKILL.md``, hidden from the ``/`` menu via
``user-invocable: false``), and invoked **inline** by
``/simsci:code-reviewer`` after it gathers PR context. A skill invoked from
another skill runs inline in the same main session — not as a sub-agent — so
``_review-core`` can spawn the ``_review_*`` fan-out itself, keeping it one
level deep. That is what lets the review be reused by other main-session
workflows (``/simsci:framework-development``'s review phase, and the ``simsci-internal``
plugin's model-development loop) without duplicating the fan-out.

``_review-core`` runs two one-level fan-outs in sequence, tiered by model. The
five review agents run on **Sonnet**; once they return, ``_review-core`` collects
every finding (the review agents' plus its own functional-correctness pass) and
spawns a second fan-out of ``_review_scorer`` agents on **Haiku** — one per
finding — to score each finding's confidence (0-100) independently of the review
agent that raised it. It then drops anything below 50 and synthesizes the
survivors. Both fan-outs stay one level deep because ``_review-core`` itself
runs inline in the main session.

Security model and recommended deny rules
=========================================

The agents in this plugin have the following shell access on Claude
Code:

- The ``_review_*`` sub-agents — the five review agents plus the
  per-finding ``_review_scorer`` — have **no Bash access at all**. They
  are fed review context by ``_review-core`` and analyze code with
  ``Read``, ``Grep``, and ``Glob`` only.
- ``_trace_extractor`` has **no Bash access** — ``Read``, ``Grep``, ``Glob``
  only. It is the one agent that deliberately reads *outside* the working
  tree: Claude Code session transcripts under ``~/.claude/projects/`` (which
  can contain anything). It returns compact digests, not transcript content,
  and is spawned only by the ``workflow-assessment`` skill.
- ``_diff_analyzer``, ``_hypothesis_tester``, and ``_split_proposer``
  declare ``Bash`` to run ``git`` and ``gh`` commands. In practice, every
  operation they perform is a read-only git command (``git diff``,
  ``git log``, ``git show``, ``git status``), which Claude Code
  auto-approves via its built-in read-only command allowlist.
  ``_split_proposer`` is additionally constrained by its own prompt to
  never run a state-changing git command.
- ``_test_writer`` and ``_feature_implementer`` are **write-capable**
  sub-agents: they declare ``Write``/``Edit`` so they can author test files and
  fill in source stub bodies during the black-box TDD build. They are
  deliberately **not** granted ``Bash`` — they never run the suite, ``git``, or
  shell commands, which confines their effect to file edits. Each runs inside
  its own git worktree (the ``/simsci:framework-development`` skill does the
  ``git worktree`` management), so neither tree contains the other's output; the
  "stay in your worktree" instruction in each brief discourages reaching across
  via an absolute path, since the worktree is not a hard read sandbox. Both are
  spawned only by ``/simsci:framework-development``.
- ``_validator`` declares ``Bash`` so it can run the package's test / lint /
  type-check commands and report a PASS/FAIL verdict. It is read-only with
  respect to source and tests — it never edits files — but running a test suite
  executes arbitrary project code, so this is a broader grant than the
  read-only git agents above. It is spawned by
  ``/simsci:framework-development`` (and by the ``simsci-internal`` plugin's
  model-development workflow when that plugin is installed).
- ``_propagate_target`` (spawned by the ``change-propagation`` skill) also
  **writes** and runs the test suite: for a target in the local repository
  it adapts files into the target package's subtree and runs that package's
  check command inside an **isolated git worktree** (its verification
  sandbox). Its prompt
  constrains it to write only within its assigned target and to **never**
  push, branch, commit, or open a PR — every durable write is the lead
  skill's, after explicit approval. For an external target it uses only
  read-only GitHub MCP calls and writes nothing.
- ``_type_hint_file`` (the type-hinter's per-file teammate) is write-capable
  within its assigned file and runs the package's mypy invocation via
  ``Bash``.
- The ``/simsci:code-reviewer``, ``/simsci:regression-debugger``,
  and ``/simsci:framework-development`` skill bodies (running in the main
  session) gather PR/repo context through the GitHub MCP server (a plugin
  dependency), falling back to read-only git/``gh`` commands when the MCP is
  unavailable; ``/simsci:framework-development`` additionally writes files
  and runs the project's check commands as it builds.

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

``allowWrite`` covers ``conda``/``pip``; the baseline assumes a conda/pip
toolchain with installs routed through IHME's Artifactory mirror — adjust
``allowWrite`` and ``allowedDomains`` for your stack (e.g. add
``files.pythonhosted.org`` for direct pip installs, or your npm registry
for Node). ``denyRead`` closes the credential-exfil path;
``network.allowedDomains`` is the egress allowlist for sandboxed Bash. That ``denyRead`` of ``gh``'s token is why the ``gh``
CLI can't run sandboxed — hence the GitHub MCP dependency, whose calls run
outside the sandbox. Even ``git push`` runs sandboxed once ``github.com``
is allowlisted and git's credential helper points at a sandbox-readable
token file — for example
``git config --global credential.helper "store --file ~/.config/git/gh-token"``
with a personal-access token in that file (any path outside ``denyRead``)
— so no un-sandboxing is needed for normal git/GitHub work.

Top-level project metadata (license, code of conduct, contributing guide)
lives at the monorepo root and applies to this tool as well; a copy of the
license ships in this directory.
