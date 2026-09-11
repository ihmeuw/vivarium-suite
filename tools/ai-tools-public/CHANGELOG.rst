**0.2.2 - 09/11/26**

- ``pr-prep``: run review-only when the user asked only for a review; when no test, lint,
  or type-check command is discoverable, ask for one, and otherwise proceed to the PR gate
  with the verdict recorded as unverified
- ``type-hinter``: preflight the agent-teams flag with a Bash check instead of inferring it
  from the tool list; fall back to spawn-brief assignments when the Task tools are absent
- ``git-rescue``: the description says not to start unasked on a noticed conflict
- ``_finalize-core``: without a team skill that covers announcing a PR, leave announcing to
  the user

**0.2.1 - 09/11/26**

- Update documentation and skills for public release

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
