---
name: pytest
description: Reference for the vivarium pytest setup. Use this skill when running, debugging, or developing automated test coverage. 
---

# Vivarium pytest

## Running tests in a vivarium repo

The recommended way to run tests is via `make test-*` targets, which include coverage reporting and built-in support for the `@slow`, `@weekly`, and `@cluster` markers. For quick iteration on a single test or when you want to skip coverage, use direct `pytest` commands. In general, you should start by testing narrowly within the scope of the current task, and run a more comprehensive make command (e.g. make check or make test-all) before major workflow points like submitting a pull request.

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


## Markers (`slow`, `cluster`, `weekly`)

`vivarium_testing_utils` auto-loads as a pytest11 plugin (no conftest import needed) and registers three markers with default-skip rules.

| Marker | Skipped unless… |
|---|---|
| `@pytest.mark.slow` | `--runslow` is passed (or `make ... RUNSLOW=true`) |
| `@pytest.mark.cluster` | `sbatch` is on PATH (i.e. running on the SLURM cluster). No CLI override — you have to actually be on the cluster. |
| `@pytest.mark.weekly` | `--runweekly` is passed **OR** today is `SLOW_TEST_DAY` (default Sunday) |

The `weekly` rule is `not (--runweekly or is_sunday)` → skip. So:
- On a regular weekday with no flag: `@weekly` tests skip.
- On a regular weekday with `--runweekly`: they run.
- On Sunday with no flag: they run (CI relies on this).

If a test is mysteriously skipping, run pytest with `-v` (or `-rs`) and read the skip reason — the plugin emits `"need --runslow option to run"`, `"not running on SLURM cluster"`, or `"not the designated slow test day for weekly tests"`.

## Coverage

Coverage is automatically outputted by `make test-*` targets. When writing a new test, be mindful of code coverage, but don't take it as a blocking priority. Use it as a way to understand whether your test is actually testing the code you think it is, and to identify any gaps in your test.


