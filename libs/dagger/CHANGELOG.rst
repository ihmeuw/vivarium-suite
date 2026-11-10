**0.1.1 - 11/04/26**

- Cap ``vivarium-dependencies`` below 3.0.0

**0.1.0 - 11/04/26**

- Initial release: Dagger moved from vivarium-cluster-tools
- Fix ``dagger restart -P/-q`` not reaching steps that took the workflow's project or queue
- Refuse a workflow whose ``simulation`` steps would write their runs to the same directory
- Finish Dagger runs that have a ``simulation`` step and were started under
  ``vivarium-cluster-tools`` 4.x before upgrading. psimulate's worker module path changed, so
  restarting such a run after the upgrade is not supported
