=============================================
Simulation Science Dev AI Tools (``simsci``)
=============================================

``simsci`` is a Claude Code plugin from the IHME Simulation Science team
providing generic AI-assisted developer workflows, usable by any IHME team in
any repository. Each skill is a scripted procedure the agent follows step by
step, stopping at explicit points for you to approve or redirect before
anything is committed, pushed, or filed. It carries no SimSci- or
vivarium-specific process: every workflow runs standalone, and the places where
a team process *could* plug in (environment setup, branch and PR conventions,
ticket filing, brainstorming and design docs, domain reference docs) are
optional seams — if an installed skill covers them, the workflow follows it;
otherwise it falls back to generic behavior.

The plugin lives under ``tools/ai-tools-public/`` in the ``vivarium-suite``
monorepo and is published through the ``vivarium-ai-tools`` marketplace whose
catalog (``.claude-plugin/marketplace.json``) lives at the monorepo root.

Which skill to use
==================

.. list-table::
   :header-rows: 1

   * - You have
     - Use
     - It ends with
   * - A finished change on a branch, ready for review
     - ``/simsci:pr-prep``
     - fixes committed one per finding, a draft PR, leftovers listed in a PR comment
   * - A feature that is not written yet
     - ``/simsci:framework-development``
     - a design, a black-box TDD build, a review, a draft PR
   * - Behavior that changed and you do not know which commit did it
     - ``/simsci:regression-debugger``
     - the causal change, named; no edits
   * - A stuck rebase or tangled history
     - ``/simsci:git-rescue``
     - repaired history, each rewrite confirmed by you
   * - A package without type hints
     - ``/simsci:type-hinter`` (needs agent teams)
     - a typed package, handed to ``commit-splitter``
   * - One large uncommitted diff
     - ``/simsci:commit-splitter``
     - small commits, and branches when needed
   * - Boilerplate to copy into many packages or repos
     - ``/simsci:change-propagation``
     - one draft PR per repository
   * - A finished agent run you want audited
     - ``/simsci:workflow-assessment``
     - a transcript-cited report; read-only

A full ``pr-prep`` or ``framework-development`` run spawns several sub-agents
and takes a while. Nothing is pushed until you approve the PR.

In more detail:

**PR Prep**

- ``/simsci:pr-prep <description>`` — takes a change you have already written on
  the current branch through to a PR ready for review, opening a new PR or
  updating the branch's existing one; the argument is optional context, not a PR
  reference. It opens with a parallel multi-agent review that fans out to
  specialist sub-agents focused on:

  - Maintainability
  - DRY
  - Structural design choices
  - Testing coverage and quality
  - Documentation

  plus its own functional-correctness pass. The five review agents run on Sonnet;
  every finding is then independently scored for confidence (0-100) by a
  per-finding ``_review_scorer`` Haiku sub-agent, and findings below 50 are
  dropped — so only verified issues reach the report, each shown with its score.

  It then proposes a **disposition per finding** (fix now / ticket / drop, each
  with a one-line why), bucketing by scope and using the confidence score only to
  break ties. The table is a reported plan, not a gate — you can re-bucket any row.
  It applies the fix-now set as **one commit per finding** and re-validates with
  the ``_validator`` sub-agent. A fix that cannot be made green is reverted and
  becomes a ticket rather than a red PR. The finish — leftover triage, the PR gate,
  the commit history, the PR, and a comment recording what went unaddressed —
  belongs to the internal ``_finalize-core`` skill.

  It requires a clean working tree, so each fix is its own revertible commit
  (a bare ``WIP`` commit is enough to satisfy that, since ``_finalize-core`` offers
  to reflow the history into reviewable commits at the end). A PR it opens is a
  **draft**; one that is already open keeps whatever state it had. Marking a draft
  ready and announcing it are separate deliberate acts it only offers.

  There is no review-only entry point: ``/simsci:code-reviewer`` was removed in
  0.2.0 and ``pr-prep`` replaced it. ``pr-prep`` edits and commits on your branch
  after printing its disposition table. For a review with no edits, say so in
  the argument (``/simsci:pr-prep review only``).

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
  on explicit user confirmation. Meant to be run by you as
  ``/simsci:git-rescue``; the confirmation gates apply however it was invoked.

**Type Hinter**

- ``/simsci:type-hinter <target>`` (a package, sub-folder, or ``.py``
  files under one package root). Runs as the **lead of an agent team**:
  resolves the inter-file dependency graph, spawns one teammate per file,
  verifies with the package's own mypy invocation, and adds ``py.typed``
  only if the package ends clean. **Requires agent teams**, an experimental
  Claude Code feature: set ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`` in the
  shell that launches ``claude`` or under ``env`` in ``~/.claude/settings.json``
  (Claude Code 2.1.32 or newer; no fallback). See "Before your first run" below.
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
  up front, and hands the finish to the shared ``_finalize-core`` skill, which
  gates PR creation on explicit user approval.

**Auto-triggering skills**

- ``/simsci:commit-splitter`` — dole out a bulk uncommitted diff into reviewable
  commits, and PR-sized branches when scope warrants.
- ``/simsci:change-propagation`` — propagate boilerplate across several targets
  (packages in the current repository and/or external repos) in parallel, one
  ``_propagate_target`` worker per target, then converge them into one draft
  PR per repo — every durable write gated on one explicit approval.
- ``/simsci:workflow-assessment`` — post-hoc audit of an agentic workflow run
  against its own definition: fans out the ``_trace_extractor`` sub-agent
  over the run's session transcripts and grades coverage, ordering/gates,
  parallelism, handoffs, tool use, and result propagation, with
  transcript-cited findings. Claude Code-only, read-only throughout.

These are slash commands too; Claude Code also loads them on its own when the
conversation matches the skill's description.

Working alongside a team plugin
===============================

``simsci`` never hard-references team tooling. Where a team process could
apply, its workflows check for an installed skill that covers it (environment
setup, branch and PR conventions, ticket filing from review findings,
structured brainstorming, design-doc drafting, domain reference docs and known
regression pitfalls, announcing a PR) and follow that skill when present. A
workflow recognizes a covering skill by its ``description``, so a team skill is
picked up when its description names the step in those terms; one skill may
cover several steps. The ``simsci-internal`` plugin in this repository is a
worked example: its ``team-conventions``, ``ticket-triage``, ``environments``,
``design-doc``, and ``brainstorming`` skills are what these seams resolve to
when both plugins are installed. Installing nothing extra works too: every
workflow falls back to generic behavior.

To pull ``simsci`` in automatically, declare it in your plugin's
``.claude-plugin/plugin.json``::

   "dependencies": [ { "name": "simsci", "marketplace": "vivarium-ai-tools" } ]

If your plugin ships from a different marketplace, that marketplace's
``marketplace.json`` also needs
``"allowCrossMarketplaceDependenciesOn": ["vivarium-ai-tools"]``, and your users
need the ``vivarium-ai-tools`` marketplace added.

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

From GitHub, inside a Claude Code session (the repository is public; no org
membership is needed):

.. code-block:: text

   /plugin marketplace add ihmeuw/vivarium-suite
   /plugin install simsci@vivarium-ai-tools

Or from a terminal, then restart Claude Code or run ``/reload-plugins`` in an
open session:

.. code-block:: shell

   claude plugin marketplace add ihmeuw/vivarium-suite
   claude plugin install simsci@vivarium-ai-tools

The same marketplace lists ``simsci-internal``, which is for the Simulation
Science team only. Other teams should install ``simsci`` alone.

For local development against a checked-out monorepo, point ``marketplace add``
at the repo root (the directory containing ``.claude-plugin/``), not at
``tools/ai-tools-public/``:

.. code-block:: text

   /plugin marketplace add /path/to/vivarium-suite
   /plugin install simsci@vivarium-ai-tools

Once installed, every skill above is a slash command: ``/simsci:pr-prep``,
``/simsci:framework-development``, ``/simsci:regression-debugger``,
``/simsci:git-rescue``, ``/simsci:type-hinter``, ``/simsci:commit-splitter``,
``/simsci:change-propagation``, and ``/simsci:workflow-assessment``.

GitHub access
-------------

The plugin's only dependency is the ``github`` plugin from Anthropic's
``claude-plugins-official`` marketplace. Claude Code installs it automatically
when that marketplace is registered, which it normally is after the first
interactive start. If ``/plugin`` shows ``simsci`` as failed to load with a
``github@claude-plugins-official`` dependency error, run
``/plugin marketplace add anthropics/claude-plugins-official``; the dependency
installs with it, and ``simsci`` loads on the next start or ``/reload-plugins``.

The ``github`` plugin's MCP server reads its token from the
``GITHUB_PERSONAL_ACCESS_TOKEN`` environment variable of the shell that
launches ``claude``. The install does not set this up; until it is set, ``/mcp``
shows the ``github`` server as failed with "Missing environment variables:
GITHUB_PERSONAL_ACCESS_TOKEN". The simplest setup is to log in with the GitHub
CLI and export its token from your shell init (``~/.zshenv`` or
``~/.bashrc``)::

   gh auth login
   export GITHUB_PERSONAL_ACCESS_TOKEN="$(gh auth token)"

For repositories in an SSO-protected org such as ``ihme-internal``, the token
must also be SSO-authorized. Restart Claude Code and confirm in ``/mcp`` that
``github`` is connected.

Only the PR-opening steps need GitHub access: ``/simsci:pr-prep``,
``/simsci:framework-development``, and ``/simsci:change-propagation``. When the
MCP is unavailable they fall back to the ``gh`` CLI, which must be logged in;
under the sandbox configuration recommended below ``gh`` cannot read its
credentials, so there the MCP is the only path. ``/simsci:git-rescue`` and
``/simsci:regression-debugger`` need only ``git``.

Before your first run
---------------------

- **Claude Code**: a current release (``claude --version``). The plugin needs
  no conda, Node, or IHME-specific tooling.
- **GitHub access**: see above. Needed only by the PR-opening steps.
- **Agent teams** (only for ``/simsci:type-hinter``): set
  ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`` under ``env`` in
  ``~/.claude/settings.json`` or export it in the shell that launches
  ``claude``, then restart. See https://code.claude.com/docs/en/agent-teams.
- **Your project's checks**: the review and TDD workflows run whatever test,
  lint, and type-check commands your repository documents, from a working
  development environment. A check that cannot run is reported as FAIL, never
  PASS.
- **Optional hardening**: the deny rules and sandbox baseline at the end of
  this file. If you enable the sandbox, read its ``git push`` note first.

Getting updates
---------------

Installs from GitHub track the ``main`` branch. To pick up changes, run
``/plugin marketplace update vivarium-ai-tools``, then from a terminal
``claude plugin update simsci@vivarium-ai-tools``, and restart Claude Code or
run ``/reload-plugins``. Release notes are in `CHANGELOG.rst <CHANGELOG.rst>`_.
To uninstall, run ``/plugin uninstall simsci@vivarium-ai-tools``.

Feedback
--------

File bugs and suggestions as `GitHub issues on ihmeuw/vivarium-suite
<https://github.com/ihmeuw/vivarium-suite/issues/new>`_, naming the skill and
your Claude Code version. Pull requests are welcome; see the monorepo's
`CONTRIBUTING.rst <../../CONTRIBUTING.rst>`_.

.. note::
   Everything below this point is reference material — the delegation
   architecture and the security model — aimed at plugin authors and at
   reviewers vetting the plugin before an install. Day-to-day use with Claude
   Code's default permission prompts needs nothing past this line. If you run
   in ``bypassPermissions`` or ``auto`` mode, or with the Bash sandbox enabled,
   read the security and sandbox sections first.

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
``/simsci:pr-prep`` after it gathers the change. A skill invoked from
another skill runs inline in the same main session — not as a sub-agent — so
``_review-core`` can spawn the ``_review_*`` fan-out itself, keeping it one
level deep. That is what lets the review be reused by other main-session
workflows (``/simsci:framework-development``'s review phase, and the ``simsci-internal``
plugin's model-development loop) without duplicating the fan-out.

The finish is defined once the same way, in the internal ``_finalize-core`` skill
(also ``user-invocable: false``, also invoked inline): leftover-finding triage, the
PR approval gate, a reviewable commit history, the draft PR, and the comment
recording what was *not* addressed. Three paths share it — ``/simsci:pr-prep``,
``/simsci:framework-development``'s Phase 5, and the ``simsci-internal`` plugin's
model-development loop — so the steps that are easy to drop when improvising, like
the backlog dedup and the not-addressed comment, are structural rather than
per-caller. Each caller keeps only what is genuinely its own: worktree teardown for
framework-development, verification-trace posting and stacked per-layer PR ordering
for model-development.

What is deliberately **not** shared is the step between review and finish — acting
on the findings. ``/simsci:framework-development`` re-dispatches each finding to the
sub-agent that wrote the code, in its own worktree, across up to three budgeted
rounds, because its implementer is blind and its first pass is expected to be
wrong. ``/simsci:pr-prep`` buckets the findings by scope and edits in the main
session, because the code already exists and was written by someone who could see
all of it and because a review of a pre-existing branch surfaces real findings
about code the change never touched, which the framework-development loop never
encounters. Same purpose, different mechanism and budget; an abstraction spanning
both would be a shell with two disjoint bodies.

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
  declare ``Bash`` to run ``git`` commands. In practice, every
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
  read-only git agents above. It is spawned by ``/simsci:pr-prep`` and
  ``/simsci:framework-development`` (and by the ``simsci-internal`` plugin's
  model-development workflow when that plugin is installed).
- ``_propagate_target`` (spawned by the ``change-propagation`` skill) also
  **writes** and runs the test suite. Each worker edits inside a local
  checkout the lead provisions (an isolated git worktree for a target in the
  reference's own repository, a local clone for a target in another
  repository) and runs that package's check command there. It has no GitHub
  or other MCP tools; anything it needs from GitHub, the lead gathers and
  passes in its brief. Its prompt constrains it to its assigned checkout and
  to **never** push, branch, commit, or open a PR — every durable write is
  the lead skill's, after explicit approval.
- ``_type_hint_file`` (the type-hinter's per-file teammate) is write-capable
  within its assigned file and runs the package's mypy invocation via
  ``Bash``.
- ``/simsci:pr-prep`` (running in the main session) reads an existing PR's
  context through the GitHub MCP server (a plugin dependency), falling back
  to read-only ``git``/``gh`` reads when the MCP is unavailable. The shared
  ``_finalize-core`` skill that it and ``/simsci:framework-development``
  invoke pushes the branch, opens the draft PR, and posts the not-addressed
  comment through the MCP, falling back to ``gh pr create`` and
  ``gh pr comment``. ``/simsci:regression-debugger`` uses only local ``git``
  and edits no files. ``/simsci:framework-development`` and
  ``/simsci:pr-prep`` additionally write files and run the project's check
  commands.
- ``/simsci:pr-prep`` is the plugin's **write-capable review follow-through**, and
  unlike the framework-development build it edits **directly in the main
  session** — there is no worktree sandbox, because the code, the tests, and the
  review all already exist and the fixes are small targeted edits rather than a
  blind build. Its containment is procedural, and worth knowing before you install
  it: nothing is edited before the per-finding disposition table is printed, so the
  full plan is on screen first; it requires a clean working tree, records the
  pre-apply commit, and lands **one commit per finding**, so any single fix is
  revertible; each fix stays within the bounded footprint that put its finding in
  the fix-now bucket, and one that outgrows it is escalated to a ticket rather than
  expanded, with the real diffstat printed at the end; and pushing and opening the
  PR happen only after the run's one approval gate, inside ``_finalize-core``,
  which stops at a draft PR — leaving an already-open PR's state untouched — and
  never marks one ready or announces it unasked. It spawns ``_validator``, which
  executes the project's test suite (see that agent above).

Each workflow skill's ``allowed-tools`` pre-approves the tools it lists,
``Bash`` included for most of them, for the turn that invokes it, so you are
not prompted per command until you next reply; the grant then clears and
prompting resumes. That makes the deny rules below the real floor against a
prompt-injected ``rm``, ``curl``, or credential read during a run, in every
permission mode. Add them before your first run rather than relying on
default-mode prompts.

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
these will block the listed commands in every permission mode. Two of them
also block steps the plugin's own skills perform: ``_finalize-core`` pushes
the branch before opening the PR, and ``git-rescue`` rebases and pushes with
``--force-with-lease``. With these rules in place the skill stops at those
steps and you run the command yourself.

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
