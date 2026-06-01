**0.4.11 - 06/01/26**

- Update vivarium imports to use new vivarium-engine package

**0.4.10 - 05/20/26**

- Update Artifact imports to use new package

**0.4.9 - 05/20/26**

- Tighten tag pattern
- Tighten setuptools include pattern (avoid matching hypothetical sibling packages)

**0.4.8 - 05/19/26**

- Update old-style imports and dependencies

**0.4.7 - 05/19/26**

- Update LICENSE file
- Remove .gitignore file
- Add package-level tests (test_package.py)

**0.4.6 - 05/18/26**

- Update init fallback version
- Add explicit 'lint' optional dependency
- Use double-quotes in Jenkinsfile

**0.4.5 - 05/15/26**

- Bump vivarium-compat dependency
- Add explicit dependency for vivarium-config-tree to prevent transitive import from vivarium
- Update imports for config_tree
- Remove unused environment.sh and artifact_requirements.txt
- Change fallback version

**0.4.4 - 05/13/26**

- Stop writing out _version file
- Remove unnecessary CODEOWNERS

**0.4.3 - 05/13/26**

- Add vivarium_testing_utils to test requirements for --runslow plugin

**0.4.2 - 05/12/26**

- Delete unused .github/ directory

**0.4.1 - 05/12/26**

- Pin vivarium_build_utils to epic/monorepo branch (temporary until all packages are migrated)

**0.4.0 - 05/11/26**

Initial release from the vivarium-suite monorepo; the standalone ``vivarium_profiling``
repository has been archived.

Breaking changes:
- Import path changed from ``vivarium_profiling`` to ``vivarium.profiling``.

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
