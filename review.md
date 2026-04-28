Now I have all sub-agent findings plus my own analysis. Here's the synthesized review:

---

## PR Review: Sbachmei/mic 7012/create monorepo

### Summary

This PR adds monorepo scaffolding for vivarium-suite, creating 9 new files atop a previously bare repository (only .gitignore and LICENSE existed). The PR implements: a GitHub Actions CI workflow with per-package change detection and matrix builds, a release workflow triggered by CHANGELOG updates, a Jenkins pipeline for scheduled builds, root-level tool configuration (pyproject.toml), and standard community files (README, CONTRIBUTING, CODE_OF_CONDUCT, PR template, CODEOWNERS).

The architecture is sound - the change-detection + matrix approach is the right pattern for a monorepo. However, several robustness issues in the shell-based CI scripts, documentation gaps, and a few security best-practice items should be addressed.

### Design

1. **JSON matrix built by shell string concatenation is fragile** - ci.yml and release.yml both construct JSON by manual string concatenation in bash. If a library directory name ever contains JSON-special characters, `fromJson()` fails with an opaque error. Consider using `python3 -c` with `json.dumps()` or `jq` (available on `ubuntu-latest`) for correct escaping and simpler logic. This also eliminates the `FIRST=true` bookkeeping.

2. **CI triggers duplicate runs for PRs** - ci.yml uses `on: push` and `on: pull_request` without branch filters. When a developer pushes to a branch with an open PR, both events fire, doubling CI cost. Common fix:
   ```yaml
   on:
     push:
       branches: [main]
     pull_request:
   ```

3. **Jenkinsfile glob is too broad** - Jenkinsfile: `findFiles(glob: '**/*/Jenkinsfile')` matches any Jenkinsfile at depth >= 2, including unintended locations. Tighten to `libs/*/Jenkinsfile` for clarity and safety.

4. **Release race condition with concurrent tag pushes** - release.yml: When multiple CHANGELOGs are updated in one push, parallel release jobs push tags concurrently. If one fails, the `workflow_dispatch` escape hatch covers recovery, but this should be documented.

5. **Docs build gated on Python 3.11 could silently never run** - ci.yml: If a package's python_versions.json doesn't include `3.11`, docs never build for that package. Consider deriving the docs version from the package's version list (e.g., first or last) or making it configurable.

### Maintainability

1. **CHANGELOG.rst first-line format is an undocumented critical contract** - release.yml: The version extraction (`head -1 | grep -oP '[0-9]+\.[0-9]+\.[0-9]+'`) and date validation (`grep -oP '\d{2}/\d{2}/\d{2}'`) depend on a specific format (e.g., `**4.1.1 - 04/21/26**`). This should be documented in CONTRIBUTING.rst or a releasing guide.

2. **Empty `VERSION` from CHANGELOG parsing proceeds silently** - release.yml: If the first line doesn't contain a semver string, `VERSION` is empty. The workflow creates a tag `vivarium-core-v` (no version). Add a guard:
   ```bash
   if [ -z "$VERSION" ]; then
     echo "::error::Could not extract version from libs/${LIB}/CHANGELOG.rst"
     exit 1
   fi
   ```

3. **`workflow_dispatch` inputs aren't validated against actual packages** - release.yml: A typo in the package name fails deep in the pipeline. Add early validation:
   ```bash
   [ -d "libs/${PACKAGE}" ] || { echo "::error::Package 'libs/${PACKAGE}' not found"; exit 1; }
   ```

4. **Missing python_versions.json causes silent CI skip** - ci.yml: If a changed lib lacks this file, the `python3 -c` call fails and produces no matrix entries, silently passing CI. Add a guard before reading versions.

5. **Hardcoded `"3.11"` for docs and Pacific timezone for date check are unexplained** - ci.yml, release.yml: Both are organizational choices with no comments. Add brief explanations.

6. **`py.typed` marker convention is undocumented** - ci.yml: The coupling between "package has `py.typed` marker" and "CI runs mypy" is a PEP 561 convention, but should be mentioned in CONTRIBUTING or the README so developers know to add it for new typed packages.

7. **Root-level pyproject.toml has placeholder overrides** - pyproject.toml: `module = []` and `disable_error_code = []` are no-ops. Either add a comment explaining they're scaffolding, or remove them until needed.

### DRY

1. **Identical `uv` installation block in both workflows** - ci.yml and release.yml. Consider using the official `astral-sh/setup-uv` action (eliminates custom code entirely) or extracting a composite action.

2. **Identical `vivarium_build_utils` installation with same FIXME** - ci.yml and release.yml. This is especially risky because the FIXME guarantees a future multi-site edit. At minimum, use a top-level `env:` variable for the branch reference.

3. **Duplicated initial-commit sentinel check** - ci.yml and release.yml. Same `0000000000000000000000000000000000000000` magic string and fallback logic with only the variable name differing. A shared script would reduce this.

4. **Matrix JSON construction pattern repeated** - Both workflows use the same `FIRST`/`MATRIX_INCLUDE` loop pattern. If the JSON-building logic is extracted into a helper (per Design #1), this also resolves the duplication.

### Tests

1. **No tests for change-detection logic** - ci.yml: The ~55 lines of bash logic (branch detection, file diffing, regex matching, matrix construction) are the keystone of the monorepo CI. A bug here silently skips packages. Consider extracting into a script and testing with `bats` or a pytest wrapper invoking it in a temp git repo.

2. **Root-change regex is too broad** - ci.yml: The pattern `^\.github/` matches CODEOWNERS and `pull_request_template.md` edits, triggering a full rebuild of all packages. Narrow to `^\.github/workflows/` (or `^\.github/(workflows|actions)/`).

3. **`workflow_dispatch` in release skips tag-existence check** - release.yml: Unlike the push path (line 67), manual dispatch doesn't check if the tag already exists. Re-dispatching an already-released version attempts to re-publish to PyPI, which would fail but wastes time.

### Documentation

1. **PyPI names and import paths are aspirational, not current** - README.md: The table lists names like `vivarium-core`, `vivarium-config-tree`, `vivarium-gbd-mapping` with imports like `import vivarium.config_tree`. The current PyPI names are `vivarium`, `layered_config_tree`, `gbd_mapping`, etc. If these are planned renames, add a note indicating these are the *target* names as part of the monorepo migration.

2. **CODE_OF_CONDUCT.rst references nonexistent `AUTHORS.md`** - CODE_OF_CONDUCT.rst: "found in this repositories AUTHORS.md" - no such file exists. Also a grammar error: should be "this repository's". Either add an `AUTHORS.md` or point to CODEOWNERS.

3. **README omits `workflow_dispatch` manual release trigger** - README.md: States releases are automatic only. The manual dispatch option is important for operators to know about.

4. **README doesn't mention `uv` despite CI using it exclusively** - README.md: The local development section shows `conda`/`pip` but the CI workflows and pyproject.toml use `uv`. Mention `uv` as the CI package manager and consider documenting it as an alternative for local development.

5. **CONTRIBUTING.rst lacks cross-reference to development setup** - CONTRIBUTING.rst: Covers branching and licensing but not how to set up an environment, run tests, or follow the CHANGELOG format. Add a reference to the README.

### Functionality

1. **Security: `workflow_dispatch` inputs are interpolated into shell** - release.yml: `${{ github.event.inputs.package }}` and `${{ github.event.inputs.version }}` are interpolated directly into bash via GitHub's expression syntax. While `workflow_dispatch` requires write access (limiting the attack surface), this is a known injection vector. Best practice is to assign to an environment variable first:
   ```yaml
   env:
     PACKAGE: ${{ github.event.inputs.package }}
     VERSION: ${{ github.event.inputs.version }}
   ```
   Then use `$PACKAGE` and `$VERSION` in the script.

2. **`workflow_dispatch` skips CHANGELOG date validation silently** - release.yml: The `if: github.event_name == 'push'` condition means manual releases bypass date validation with no comment explaining why. Add a brief comment.

3. **`find libs` with `2>/dev/null` suppresses real errors** - ci.yml: If libs doesn't exist (e.g., on a branch that deletes it), the error is silently swallowed and CI passes with "no changes." Consider logging when `ALL_LIBS` is empty.

4. **FIXME[MIC-7015] pins both workflows to an unmerged feature branch** - ci.yml, release.yml: If this PR merges before `poc-monorepo` lands on `main`, both CI and release depend on a potentially force-pushed or deleted branch. Ensure MIC-7015 is tracked as a merge blocker or follow-up.

### Minor Nits

1. pull_request_template.md: The `Category` list uses an em dash in `CI/infrastructure` - the template comment should use a regular hyphen per team conventions.
2. Jenkinsfile: `@Library("get_vbu_version") _` doesn't pin a branch. The POC version (Jenkinsfile) used `@Library("get_vbu_version@main")`. Consider pinning to `main` for reproducibility.
3. release.yml: Date validation uses 2-digit year (`%y`). Consider 4-digit (`%Y`) for clarity, matching in both CHANGELOG convention and validation.

### Overall

The monorepo architecture and CI design are well-structured. The change-detection + matrix pattern is the right approach, and the release workflow's CHANGELOG-driven automation is a clean design. The highest-priority items to address before merging are:

- **Robustness**: Empty version extraction (Design/Maintainability), missing python_versions.json handling, and `workflow_dispatch` input validation
- **DRY**: Extract `uv` + `vivarium_build_utils` installation (especially given the pending FIXME)
- **Security**: Move `workflow_dispatch` inputs to environment variables
- **Documentation**: Clarify that README package names are aspirational; fix the `AUTHORS.md` reference; document the CHANGELOG format contract