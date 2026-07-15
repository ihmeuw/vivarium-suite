=================
Vivarium AI Tools
=================

Vivarium AI Tools (``viv``) is a Claude Code plugin providing SimSci- and
vivarium-specific agent workflows. The plugin lives under ``tools/ai-tools/``
in the ``vivarium-suite`` monorepo and is published through the
``vivarium-ai-tools`` marketplace whose catalog
(``.claude-plugin/marketplace.json``) lives at the monorepo root.

``viv`` declares the ``viv-public`` plugin (``tools/ai-tools-public/``) as a
dependency: installing ``viv`` automatically installs and enables the generic
workflows too — multi-agent code review, git rescue, commit splitting, type
hinting, regression debugging, change propagation, workflow assessment, and the
framework-development TDD loop all live in ``viv-public`` under the
``/viv-public:`` namespace (see that plugin's README). ``viv`` adds the
team-specific layer on top, and ``viv-public``'s optional seams (branch and PR
conventions, ticket filing, environment setup) resolve automatically to the
team skills below when both plugins are enabled.

It includes:

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
  ``/viv-public:framework-development``. The verification is an internal loop for
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
- ``ticket-triage`` — turn code-review findings (e.g. from
  ``/viv-public:code-reviewer``) that are out of scope for the current PR into
  Jira ticket recommendations, deduplicated against the MIC backlog via the
  ``_duplicate_finder`` sub-agent.
- ``repo-maintenance`` — audit both plugins' AI plaintext (skills, agents,
  READMEs, root ``CLAUDE.md``) for drift against upstream sources via
  per-unit ``_claim_auditor`` sub-agents; fixes are gated on user approval.

Loaded automatically when the context is relevant to the skill's description.

Layout
======

The marketplace catalog lives at the monorepo root; the plugin itself lives under
``tools/ai-tools/``:

- ``<repo-root>/.claude-plugin/marketplace.json``: marketplace catalog listing
  this plugin (``"source": "./tools/ai-tools"``) and ``viv-public``
  (``"source": "./tools/ai-tools-public"``). Claude Code requires the
  marketplace catalog at the repo root for
  ``/plugin marketplace add ihmeuw/vivarium-suite`` to find it.
- ``tools/ai-tools/.claude-plugin/plugin.json``: plugin manifest, including the
  ``viv-public`` dependency.
- ``tools/ai-tools/agents/``: specialist sub-agents spawned by the skills.
- ``tools/ai-tools/skills/``: Claude Code skills (workflow entry points and
  model-loaded reference material).
- ``tools/ai-tools/CHANGELOG.rst``: history of plugin changes.

Top-level project metadata (license, code of conduct, contributing guide) lives at the
monorepo root and applies to this tool as well.

Installing in Claude Code
=========================

From GitHub:

.. code-block:: shell

   /plugin marketplace add ihmeuw/vivarium-suite
   /plugin install viv@vivarium-ai-tools

Installing ``viv`` resolves its dependencies automatically: ``viv-public``
(same marketplace) plus the ``slack`` and ``github`` plugins from the official
marketplace. One install, everything enabled.

For local development against a checked-out monorepo, point ``marketplace add``
at the repo root (the directory containing ``.claude-plugin/``), not at
``tools/ai-tools/``:

.. code-block:: shell

   /plugin marketplace add /path/to/vivarium-suite
   /plugin install viv@vivarium-ai-tools

Once installed, the team-specific entry point is ``/viv:model-development``;
the generic entry points (``/viv-public:code-reviewer``,
``/viv-public:framework-development``, ``/viv-public:git-rescue``, and friends)
come from the ``viv-public`` dependency. Cross-plugin references are always
namespaced: ``viv``'s workflows spawn ``viv-public``'s sub-agents as
``viv-public:<agent>``, and bare agent names do not resolve across plugins.

Delegation mechanism and security model
=======================================

The fan-out architecture (main-session orchestration, the shared
``_review-core`` review skill, model tiering, and per-agent tool grants) is
documented in the ``viv-public`` README — most of the sub-agents now live
there. ``/viv:model-development`` reuses that machinery inline: it spawns
``viv-public:_validator`` and the ``viv-public:_review_*`` agents and invokes
the ``viv-public:_review-core`` skill for its review phase.

The agents that ship in *this* plugin:

- ``_claim_auditor`` has **no Bash access** — it verifies plaintext claims
  with ``Read``/``Grep``/``Glob``, read-only MCP calls (hub, Jira, Slack,
  Jenkins, GitHub), and ``WebFetch`` only.
- ``_duplicate_finder`` has **no shell or file access at all** — its only
  tools are the read-only Jira MCP ``search`` and ``get_issue`` calls it
  uses to check candidate tickets against the backlog.
- ``_model_implementer`` and ``_vv_writer`` (the ``model-development`` workflow)
  are write-capable (``Write``/``Edit``, no ``Bash``): they author
  files but never run the simulation, ``git``, or shell commands, and their
  writes are partitioned. ``_model_implementer`` writes **only its assigned model
  layer's code** (artifact, component, or observer) — never the verification checks or notebook —
  dispatched once per layer and working directly on the feature branch (the
  stages are sequential, so no worktree isolation is needed). ``_vv_writer``
  writes **the verification checks and notebook**, blind to the implementation. Both
  are spawned only by the ``/viv:model-development`` skill.

The recommended deny rules and sandbox baseline in the ``viv-public`` README
apply to this plugin unchanged.
