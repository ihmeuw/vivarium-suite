**0.4.1 - 05/12/26**

- Pin vivarium_build_utils to epic/monorepo branch (temporary until all packages are migrated)

**0.4.0 - 05/11/26**

BREAKING CHANGE: Initial release from vivarium-suite monorepo. The import path is 
now ``vivarium.profiling`` (was ``vivarium_profiling``). The ``vivarium-compat``
shim redirects the old path with a ``DeprecationWarning``; update imports before
that shim is removed.

**0.3.6 - 05/05/26**

Archive notice: this package is archived and no longer maintained. Please use the 
vivarium-suite monorepo for future development. Refer to the README for more details.

**0.3.5 - 04/21/26**

- Update for VPH v5

**0.3.4 - 04/16/26**

- Tighten vivarium_build_utils pin

**0.3.3 - 04/15/26**

- Update vivarium_build_utils pin

**0.3.2 - 03/25/26**

- Remove upstream_repos from Jenkinsfile

**0.3.1 - 01/27/2026**

- Adjust the parameters for run_benchmark command
- Update makefile

**0.3.0 - 01/22/2026**

- Convert data ETL to click commands
- add configurable function call patterns
- add notebook support

**0.2.0 - 01/02/2026**

- Add MultiComponentParser with Causes, Risks, RiskEffects

**0.1.0 - 10/24/2025**

- Convert bash script to python
- add profiling CLI with scalene backend

**0.0.1 - 10/20/2025**

- Initial release
