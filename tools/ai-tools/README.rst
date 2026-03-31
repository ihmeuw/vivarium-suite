=================
Vivarium AI Tools
=================

Vivarium AI Tools is a local agent plugin-style repository for GitHub Copilot customization.

It includes:

**Code Reviewer**

- ``code_reviewer`` — orchestrator that delegates to specialist sub-agents
- ``_review_maintainability`` - sub-agent that reviews readability, documentation, implicit assumptions, and coupling
- ``_review_dry`` - sub-agent that identifies duplicated logic, missed abstractions, and repeated patterns
- ``_review_design`` - sub-agent that evaluates data structure choices, algorithmic efficiency, API surface, and representation trade-offs
- ``_review_tests`` - sub-agent that assesses test coverage, test quality, edge cases, and test naming and structure
- ``_review_documentation`` - sub-agent that checks docstring quality, accuracy, and completeness, as well as comments and changelog updates

**Regression Debugger**

- ``model_regression_debugger`` — orchestrator that traces data pipeline changes across repos to find the cause of simulation regressions
- ``_diff_analyzer`` — sub-agent that analyzes diffs in a single repo (run in parallel for multi-repo changes)
- ``_hypothesis_tester`` — sub-agent that tests a single hypothesis about a regression cause

Repository Layout
=================

- ``plugin.json``: plugin metadata
- ``agents/``: custom agent definitions
- project metadata files copied from ``vivarium_dependencies``

Using as a local plugin
=======================

To register this as a local plugin in VS Code, add this path to ``chat.pluginLocations`` in settings:

.. code-block:: json

   {
     "chat.pluginLocations": {
       "/your/path/to/vivarium_ai_tools": true
     }
   }

Then reload VS Code and verify the plugin appears in the Agent Plugins UI.
