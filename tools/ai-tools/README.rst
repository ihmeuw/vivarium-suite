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

- ``/viv:code-reviewer <PR or description>`` — parallel multi-agent review that fans out to specialist sub-agents focused on:
  
  - Maintainability
  - DRY
  - Structural design choices
  - Testing coverage and quality
  - Documentation

  plus its own functional-correctness pass. The five review agents run on Sonnet;
  every finding is then independently scored for confidence (0-100) by a
  per-finding ``_review_scorer`` Haiku sub-agent, and findings below 50 are
  dropped — so only verified issues reach the report, each shown with its score.

After the review, findings the user won't address in the current PR can be
handed to the ``ticket-triage`` skill (see Skills below), to compile and file non-duplicate JIRA tickets.

**Regression Debugger**

- ``/viv:model-regression-debugger <symptom and context>`` — traces data pipeline changes across repos to find the cause of simulation regressions.

**Git Rescue**

- ``/viv:git-rescue [optional description]``.
  Diagnoses and untangles messy git situations — stuck interactive
  rebases, stacked-branch conflicts after a squash-merge of the parent,
  divergent history, accidental merge commits, dropped commits. Always
  creates a backup ref before rewriting history and gates every
  destructive step (including the final ``git push --force-with-lease``)
  on explicit user confirmation. User-invoked only — there is no
  auto-trigger.

**Type Hinter**

- ``/viv:type-hinter <target>`` (a
  package, sub-folder, or ``.py`` files under one ``libs/<pkg>/``). Runs
  as the **lead of an agent team**: resolves the inter-file dependency
  graph, spawns one teammate per file, verifies with ``make mypy``, and
  adds ``py.typed`` only if the package ends clean. **Requires agent
  teams** (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``, v2.1.32+; no
  fallback). It **writes**, then hands the diff to
  ``/viv:commit-splitter``.

**Framework Development**

- ``/viv:framework-development <ticket or feature description>`` — an end-to-end
  design → implement → verify → PR loop on a single well-scoped framework
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

**Model Development**

- ``/viv:model-development <ticket, research doc, or iteration description>`` — an
  end-to-end iteration of a vivarium model concept (a cause, risk, intervention,
  or observer) in an existing model repo, driven from its vivarium-research
  documentation. The main session owns the **iteration plan** — the contract
  that pins the artifact keys, pipeline names, state-table columns, and observer
  outputs a change touches, plus the quantitative expectations from the research
  doc. From that contract it runs a **sequential build** in
  data-dependency order (artifact, then component, then observer — skipping
  layers the change doesn't touch), while an **internal verification** is authored in
  parallel and blind to the implementation — InteractiveContext checks and a
  notebook built from the plan alone. It then **runs the simulation** to verify
  (the repo's existing suite, the new checks, a local ``simulate`` run, and the
  notebook) and applies the same multi-lens review as
  ``/viv:framework-development``. The verification is an internal loop for
  engineering confidence — not formal V&V — so its **traces** (notebook plots and
  tables) are posted to the PR, while the artifacts themselves stay out of the
  repo unless you ask to keep them. Gates the artifact build and the PR on
  explicit user approval.

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
  via the Read the Docs search API.
- ``design-doc`` — SimSci Engineering convention for drafting a design
  document on the IHME hub
- ``brainstorming`` — structured design exploration that produces a Jira
  plan comment, a new Jira ticket, or a Confluence design doc; ships a
  browser-based Mermaid diagramming companion
- ``commit-splitter`` — dole out a bulk uncommitted diff into reviewable
  commits, and PR-sized branches when scope warrants.
- ``ticket-triage`` — turn code-review findings that are out of scope for
  the current PR into Jira ticket recommendations.
- ``repo-maintenance`` — audit the plugin's AI plaintext (skills, agents,
  commands, README, root ``CLAUDE.md``) for drift against upstream
  sources via per-unit ``_claim_auditor`` sub-agents; fixes are gated on
  user approval.
- ``change-propagation`` — propagate boilerplate across several targets (monorepo libs and/or external
  repos) in parallel, one ``_propagate_target`` worker per target, then
  converge them into one draft PR per repo — every durable write gated on
  one explicit approval.
- ``workflow-assessment`` — post-hoc audit of an agentic workflow run
  against its own definition: fans out the ``_trace_extractor`` sub-agent
  over the run's session transcripts and grades coverage, ordering/gates,
  parallelism, handoffs, tool use, and result propagation, with
  transcript-cited findings. Claude Code-only, read-only throughout.

Loaded automatically when the context is relevant to the skill's description.

Layout
======

The marketplace catalog lives at the monorepo root; the plugin itself lives under
``tools/ai-tools/``:

- ``<repo-root>/.claude-plugin/marketplace.json``: marketplace catalog. Its single
  plugin entry uses ``"source": "./tools/ai-tools"`` to point at the plugin
  directory. Claude Code requires the marketplace catalog at the repo root for
  ``/plugin marketplace add ihmeuw/vivarium-suite`` to find it.
- ``tools/ai-tools/.claude-plugin/plugin.json``: plugin manifest.
- ``tools/ai-tools/agents/``: specialist sub-agents spawned by the slash commands.
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

Once installed, the entry points are the slash commands
``/viv:code-reviewer``, ``/viv:model-regression-debugger``, and
``/viv:framework-development``. These run the sub-agent fan-out at
main-session level and produce a multi-agent review, a regression
investigation, or an end-to-end feature build.

Delegation mechanism
====================

The parallel fan-out runs at main-session level. That is what the
``commands/*.md`` slash commands do: their ``allowed-tools: Agent(...)``
field grants the main session permission to spawn the listed
``_review_*`` (or ``_diff_analyzer`` / ``_hypothesis_tester``)
sub-agents in parallel, and the slash command body is itself the
orchestration prompt.

The multi-agent review fan-out is defined once, in the internal ``_review-core``
skill (``skills/_review-core/SKILL.md``, hidden from the ``/`` menu via
``user-invocable: false``), and invoked **inline** by ``/viv:code-reviewer``
after it gathers PR context. A skill invoked from a command runs inline in the
same main session — not as a sub-agent — so ``_review-core`` can spawn the
``_review_*`` fan-out itself, keeping it one level deep. That is what lets the
review be reused by other main-session commands without duplicating the
fan-out.

``_review-core`` runs two one-level fan-outs in sequence, tiered by model. The
five review agents run on **Sonnet**; once they return, ``_review-core`` collects
every finding (the review agents' plus its own functional-correctness pass) and spawns a
second fan-out of ``_review_scorer`` agents on **Haiku** — one per finding — to
score each finding's confidence (0-100) independently of the review agent that raised it.
It then drops anything below 50 and synthesizes the survivors. Both fan-outs stay
one level deep because ``_review-core`` itself runs inline in the main session.


Security model and recommended deny rules
=========================================

The agents in this plugin have the following shell access on Claude
Code:

- The ``_review_*`` sub-agents — the five review agents plus the
  per-finding ``_review_scorer`` — have **no Bash access at all**. They
  are fed review context by ``_review-core`` and analyze code with
  ``Read``, ``Grep``, and ``Glob`` only.
- ``_claim_auditor`` likewise has **no Bash access** — it verifies
  plaintext claims with ``Read``/``Grep``/``Glob``, read-only MCP calls
  (hub, Jira, Slack, Jenkins, GitHub), and ``WebFetch`` only.
- ``_duplicate_finder`` has **no shell or file access at all** — its only
  tools are the read-only Jira MCP ``search`` and ``get_issue`` calls it
  uses to check candidate tickets against the backlog.
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
- ``_test_writer`` and ``_feature_implementer`` are the plugin's first
  **write-capable** sub-agents: they declare ``Write``/``Edit`` so they can
  author test files and fill in source stub bodies during the black-box TDD
  build. They are deliberately **not** granted ``Bash`` — they never run the
  suite, ``git``, or shell commands, which confines their effect to file edits.
  Each runs inside its own git worktree (the ``/viv:framework-development``
  command does the ``git worktree`` management), so neither tree contains the
  other's output; the "stay in your worktree" instruction in each brief
  discourages reaching across via an absolute path, since the worktree is not a
  hard read sandbox. Both are spawned only by the ``/viv:framework-development``
  slash command.
- ``_model_implementer`` and ``_vv_writer`` (the ``model-development`` workflow)
  are likewise write-capable (``Write``/``Edit``, no ``Bash``): they author
  files but never run the simulation, ``git``, or shell commands, and their
  writes are partitioned. ``_model_implementer`` writes **only its assigned model
  layer's code** (artifact, component, or observer) — never the verification checks or notebook —
  dispatched once per layer and working directly on the feature branch (the
  stages are sequential, so no worktree isolation is needed). ``_vv_writer``
  writes **the verification checks and notebook**, blind to the implementation. Both
  are spawned only by the ``/viv:model-development`` skill.
- ``_validator`` declares ``Bash`` so it can run the package's ``make
  test-*`` / ``make lint`` / ``make mypy`` targets and report a PASS/FAIL
  verdict. It is read-only with respect to source and tests — it never edits
  files — but running a test suite executes arbitrary project code, so this is a
  broader grant than the read-only git agents above. It is spawned by the
  ``/viv:framework-development`` and ``/viv:model-development`` workflows.
- ``_propagate_target`` (spawned by the ``change-propagation`` skill) also
  **writes** and runs the test suite: for a monorepo target it adapts files
  into a ``libs/<pkg>/`` subtree and runs that package's ``make check`` inside
  an **isolated git worktree** (its verification sandbox). Its prompt constrains
  it to write only within its assigned target and to **never** push, branch,
  commit, or open a PR — every durable write is the lead skill's, after explicit
  approval. For an external target it uses only read-only GitHub MCP calls and
  writes nothing.
- The ``/viv:code-reviewer``, ``/viv:model-regression-debugger``,
  ``/viv:framework-development``, and ``/viv:model-development`` slash command and
  skill bodies (running in the main session) gather PR/repo context through the
  GitHub MCP server (a plugin dependency; see the ``plugin-setup`` skill),
  falling back to read-only git/``gh`` commands when the MCP is unavailable;
  ``/viv:framework-development`` and ``/viv:model-development`` additionally write
  files and run make/sim targets as they build.

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
