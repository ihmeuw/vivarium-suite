**0.3.0 - 10/07/26**

- Add the ``writing-style`` skill: rules for prose that people read, based on
  ASD-STE100 Simplified Technical English, with a bundled ``ste_check.py`` checker,
  before-and-after examples, and ``check_agent_blocks.py`` to keep the agent copies of
  the rules the same
- Invoke ``writing-style`` from ``_finalize-core``, ``commit-splitter``, and
  ``change-propagation`` before they post PR, commit, or comment text
- Add a plain-English prose line to the ``_review_*`` agents and ``_split_proposer``

**0.2.1 - 09/16/26**

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
