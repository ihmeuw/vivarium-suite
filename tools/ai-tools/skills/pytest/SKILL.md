---
name: pytest
description: Reference for the vivarium pytest setup. Use this skill when running, debugging, or developing automated test coverage. 
---

# Vivarium pytest

## Running tests in a vivarium repo

The recommended way to run tests is via `make test-*` targets, which parallelize with `pytest-xdist` and add built-in support for several markers (plus opt-in coverage; see below). For quick iteration on a single test, running individual pytest commands is acceptable. In general, you should start by testing narrowly within the scope of the current task, and run a more comprehensive make command (e.g. make test-all) before major workflow points like submitting a pull request.

**`make test-*` targets:**

```
make test-all              # everything
make test-unit             # only tests/unit/
make test-integration      # only tests/integration/
make test-e2e              # only tests/e2e/
```

Each target runs `pytest -vvv -n auto tests/<type>`. To enable `@slow` and/or `@weekly` tests, pass them as make variables (NOT pytest flags):

```
make test-all RUNSLOW=true RUNWEEKLY=true
```

`make test-unit`/`-integration`/`-e2e` only work in repos that actually have the matching `tests/<type>/` subdir — most vivarium repos don't (test layouts are flat or domain-organized). Check `ls tests/` first; otherwise fall back to `make test-all` or direct pytest.


## Test organization

- **Mirror the source layout.** Add tests to the `test_<module>.py` file that parallels the source module you're changing (e.g. tests for `results/observation.py` go in `tests/.../results/test_observation.py`). Don't create a per-feature test file — the team organizes tests by the code under test, not by feature.
- **Group a unit's tests in a class with shared setup.** When several tests exercise one unit or scenario, put them in a `class Test…:` and lift the common setup into a fixture or helper (DRY), but keep each distinct requirement in its own test method — e.g. one test asserts dtype/category order, another asserts numeric columns stay numeric. Shared setup, asserts doled out per test. (This is a common preference, not universal across existing code — match the surrounding file when it clearly does otherwise.)

## Markers

`vivarium_testing_utils` auto-loads as a pytest plugin and registers markers with default-skip rules. Look at `vivarium/testing_utils/pytest_plugin.py` (in `libs/testing-utils/`) to understand their behavior. If a test is mysteriously skipping, you may also run pytest with `-v` (or `-rs`) and read the skip reason.

## Coverage

Coverage is opt-in: pass `COV=1` to any `make test-*` target (e.g. `make test-all COV=1`) to add `--cov --cov-report term --cov-report html:./output/htmlcov_<type>`. Without it, no coverage is collected. When writing a new test, be mindful of code coverage, but don't take it as a blocking priority. Use it as a way to understand whether your test is actually testing the code you think it is, and to identify any gaps in your test.


