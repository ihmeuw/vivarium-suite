---
name: pytest
description: Reference for the vivarium pytest setup. Use this skill when running, debugging, or developing automated test coverage. 
---

# Vivarium pytest

## Running tests in a vivarium repo

The recommended way to run tests is via `make test-*` targets, which include coverage reporting and built-in support for several markers. For quick iteration on a single test, running individual pytest commands is acceptable. In general, you should start by testing narrowly within the scope of the current task, and run a more comprehensive make command (e.g. make check or make test-all) before major workflow points like submitting a pull request.

**`make test-*` targets:**

```
make check                 # formatting, typecheck, and test (fast tests only)
make test-all              # everything
make test-unit             # only tests/unit/
make test-integration      # only tests/integration/
make test-e2e              # only tests/e2e/
```

Each target runs `pytest -vvv --cov --cov-report term --cov-report html:./output/htmlcov_<type> tests/<type>`. To enable `@slow` and/or `@weekly` tests, pass them as make variables (NOT pytest flags):

```
make test-all RUNSLOW=true RUNWEEKLY=true
```

`make test-unit`/`-integration`/`-e2e` only work in repos that actually have the matching `tests/<type>/` subdir — most vivarium repos don't (test layouts are flat or domain-organized). Check `ls tests/` first; otherwise fall back to `make test-all` or direct pytest.


## Markers

`vivarium_testing_utils` auto-loads as a pytest plugin and registers markers with default-skip rules. Look at `vivarium_testing_utils/pytest_plugin.py` to understand their behavior. If a test is mysteriously skipping, you may also run pytest with `-v` (or `-rs`) and read the skip reason.

## Coverage

Coverage is automatically outputted by `make test-*` targets. When writing a new test, be mindful of code coverage, but don't take it as a blocking priority. Use it as a way to understand whether your test is actually testing the code you think it is, and to identify any gaps in your test.


