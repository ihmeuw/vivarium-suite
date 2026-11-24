**0.2.0 - 10/28/26**

- Add the backend, the python_step frontend, and the test harness.
- Pin the ``cluster`` extra to ``jobmon_installer_ihme`` 10.12.2
- Record each step of a build through Jobmon a few seconds after the step finishes, so a
  build that is killed keeps the steps it had recorded
- Add a hash of the code of a Python file or a notebook that comments and formatting do not
  change

**0.1.2 - 11/24/26**

- Fix the Read the Docs build, which read cluster-tools' version together with the date
  on its CHANGELOG line

**0.1.1 - 11/17/26**

- Cap ``vivarium-dependencies`` below 3.0.0

**0.1.0 - 11/04/26**

- Initial release: Dagger moved from vivarium-cluster-tools
- Fix ``dagger restart -P/-q`` not reaching steps that took the workflow's project or queue
- Refuse a workflow whose ``simulation`` steps would write their runs to the same directory
- Finish Dagger runs that have a ``simulation`` step and were started under
  ``vivarium-cluster-tools`` 4.x before upgrading. psimulate's worker module path changed, so
  restarting such a run after the upgrade is not supported
