**0.4.0 - TBD/TBD/26**

- Add a ``_test_summarizer`` agent that writes the PR's Testing section. It links
  each testing-strategy item to the test that covers it and lists the items no
  test covers. ``_finalize-core`` runs it for ``/simsci:pr-prep`` and
  ``/simsci:framework-development``, and takes optional strategy items and
  outside-suite checks from its caller

**0.3.1 - 10/13/26**

- Add a short testing strategy section to the ``/simsci:framework-development`` design proposal

**0.3.0 - 10/13/26**

- Add a one-line plain-English writing rule, based on ASD-STE100 Simplified
  Technical English, to the five ``_review_*`` agents and ``_split_proposer``
  (`MIC-7606 <https://jira.ihme.washington.edu/browse/MIC-7606>`_)

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
