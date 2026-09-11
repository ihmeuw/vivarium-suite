**0.2.1 - 09/11/26**

- README: add "Which skill to use", "GitHub access" (the ``github`` plugin's MCP server
  needs ``GITHUB_PERSONAL_ACCESS_TOKEN``), "Before your first run", "Getting updates", and
  "Feedback" sections; note that ``pr-prep`` replaced the removed review-only command;
  correct stale claims about which skills use the GitHub MCP, which spawn ``_validator``,
  and how ``_propagate_target`` works; describe what ``allowed-tools`` pre-approves and
  which deny rules block the plugin's own push and rebase steps
- ``pr-prep``: run review-only when the user asked only for a review; confirm GitHub access
  before the review; ask for check commands when none are discoverable; drop the Jira
  reference
- ``type-hinter``: preflight the agent-teams flag directly instead of inferring it from the
  tool list
- ``_finalize-core`` and ``commit-splitter``: timestamp backup branches so a second run does
  not fail; ``_finalize-core`` hands over the PR body when neither the GitHub MCP nor ``gh``
  is available
- ``git-rescue``: say in the description when not to start unasked; ``change-propagation``:
  add an argument hint
- Manifest: add author, homepage, repository, and license; drop the unrecognized
  ``publisher`` field

**0.2.0 - 08/06/26**

- **Breaking:** Remove ``/simsci:code-reviewer``. There is no longer a review-only entry point.
- Implement new ``/simsci:pr-prep`` skill which carries a change you have already written
  from review through fixes and triage to a PR.
- Extract ``framework-development``'s finish sequence to a new ``_finalize-core`` skill
  (which is now used by ``framework-development``, the new ``pr-prep`` skill, and
  ``simsci-internal``'s ``model-development``)

**0.1.0 - 07/27/26**

- Initial release: generic developer tooling extracted from the ``simsci-internal`` plugin (`MIC-7220 <https://jira.ihme.washington.edu/browse/MIC-7220>`_)
- Ships the multi-agent code review family (``/simsci:code-reviewer``), ``git-rescue``,
  ``commit-splitter``, ``type-hinter``, ``regression-debugger``, ``workflow-assessment``,
  ``change-propagation``, and ``framework-development``
