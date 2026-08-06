===========================================================
Simulation Science Internal AI Tools (``simsci-internal``)
===========================================================

Simulation Science Internal AI Tools (``simsci-internal``) is a Claude Code plugin providing SimSci- and
vivarium-specific agent workflows. The plugin lives under ``tools/ai-tools/``
in the ``vivarium-suite`` monorepo and is published through the
``vivarium-ai-tools`` marketplace whose catalog
(``.claude-plugin/marketplace.json``) lives at the monorepo root.

``simsci-internal`` declares the ``simsci`` plugin (``tools/ai-tools-public/``) as a
dependency: installing ``simsci-internal`` automatically installs and enables the generic
workflows too — the review-to-PR ``pr-prep`` loop, git rescue, commit splitting, type
hinting, regression debugging, change propagation, workflow assessment, and the
framework-development TDD loop all live in ``simsci`` under the
``/simsci:`` namespace (see that plugin's README). ``simsci-internal`` adds the
team-specific layer on top, and ``simsci``'s optional seams (branch and PR
conventions, ticket filing, environment setup) resolve automatically to the
team skills below when both plugins are enabled.

It includes:

**Model Development**

- ``/simsci-internal:model-development <ticket, research doc, or iteration description>`` — an
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
  ``/simsci:framework-development``. The verification is an internal loop for
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
  ``pytest-vivarium``, and where baked-in coverage output lands.
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
  ``/simsci:pr-prep``) that are out of scope for the current PR into
  Jira ticket recommendations, deduplicated against the MIC backlog via the
  ``_duplicate_finder`` sub-agent. It is also the skill ``simsci``'s
  ``_finalize-core`` seam resolves to, so every path that opens a PR —
  ``/simsci:pr-prep``, ``/simsci:framework-development``, and
  ``model-development`` — routes its leftover findings here. When the caller has
  already drawn the scope line, it takes that as given rather than re-asking.
- ``repo-maintenance`` — audit both plugins' AI plaintext (skills, agents,
  READMEs, root ``CLAUDE.md``) for drift against upstream sources via
  per-unit ``_claim_auditor`` sub-agents; fixes are gated on user approval.

Loaded automatically when the context is relevant to the skill's description.

Layout
======

The marketplace catalog lives at the monorepo root; the plugin itself lives under
``tools/ai-tools/``:

- ``<repo-root>/.claude-plugin/marketplace.json``: marketplace catalog listing
  this plugin (``"source": "./tools/ai-tools"``) and ``simsci``
  (``"source": "./tools/ai-tools-public"``). Claude Code requires the
  marketplace catalog at the repo root for
  ``/plugin marketplace add ihmeuw/vivarium-suite`` to find it.
- ``tools/ai-tools/.claude-plugin/plugin.json``: plugin manifest, including the
  ``simsci`` dependency.
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
   /plugin install simsci-internal@vivarium-ai-tools

Installing ``simsci-internal`` resolves its dependencies automatically: ``simsci``
(same marketplace) plus the ``slack`` and ``github`` plugins from the official
marketplace. One install, everything enabled.

For local development against a checked-out monorepo, point ``marketplace add``
at the repo root (the directory containing ``.claude-plugin/``), not at
``tools/ai-tools/``:

.. code-block:: shell

   /plugin marketplace add /path/to/vivarium-suite
   /plugin install simsci-internal@vivarium-ai-tools

Once installed, the team-specific entry point is ``/simsci-internal:model-development``;
the generic entry points (``/simsci:pr-prep``,
``/simsci:framework-development``, ``/simsci:git-rescue``, and friends)
come from the ``simsci`` dependency. Cross-plugin references are always
namespaced: ``simsci-internal``'s workflows spawn ``simsci``'s sub-agents as
``simsci:<agent>``, and bare agent names do not resolve across plugins.

Delegation mechanism and security model
=======================================

The fan-out architecture (main-session orchestration, the shared
``_review-core`` review skill, model tiering, and per-agent tool grants) is
documented in the ``simsci`` README — most of the sub-agents now live
there. ``/simsci-internal:model-development`` reuses that machinery inline: it spawns
``simsci:_validator`` and the ``simsci:_review_*`` agents, invokes
the ``simsci:_review-core`` skill for its review phase, and hands its finish to the
``simsci:_finalize-core`` skill — which routes leftover findings back into this
plugin's ``ticket-triage`` and the PR itself through ``team-conventions``, since
``simsci``'s seams resolve here when both plugins are enabled.

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
  are spawned only by the ``/simsci-internal:model-development`` skill.

The recommended deny rules and sandbox baseline in the ``simsci`` README
apply to this plugin unchanged.
