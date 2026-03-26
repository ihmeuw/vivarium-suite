=================
Vivarium AI Tools
=================

Vivarium AI Tools is a local agent plugin-style repository for GitHub Copilot customization.

It includes a Python code review orchestrator and specialist sub-agents:

- ``code_reviewer``
- ``review_maintainability``
- ``review_dry``
- ``review_design``
- ``review_tests``
- ``review_documentation``

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
       "/home/pnast/repos/vivarium_ai_tools": true
     }
   }

Then reload VS Code and verify the plugin appears in the Agent Plugins UI.
