---
name: pytest
description: Reference for the vivarium pytest setup — the `make test-*` entry points, the `slow` / `cluster` / `weekly` markers silently registered by the auto-loaded `vivarium_testing_utils` pytest plugin, the day-of-week gate on `weekly`, the make-var → pytest-flag conversion of `RUNSLOW`/`RUNWEEKLY`, and where baked-in coverage output lands. Use whenever the task involves running tests in a vivarium repo, narrowing or widening pytest scope, debugging "why is this test skipped", running slow or weekly tests, or finding coverage output. Trigger on phrases like "run the tests", "run the unit tests", "make test-all", "make test-unit", "make test-integration", "make test-e2e", "test-all RUNSLOW=true", "--runslow", "--runweekly", "RUNSLOW", "RUNWEEKLY", "slow tests skipped", "weekly tests", "cluster marker", "test coverage", "htmlcov", "pytest in this repo".
---

# Vivarium pytest

## What this skill covers

The vivarium-specific pytest conventions: the `make test-*` entry points, the markers and skip rules `vivarium_testing_utils` silently registers, and where the always-on coverage output ends up.

**Out of scope** (other skills / `pytest --help` cover these):
- Detailed `make` target semantics → `make-commands` (it already documents the `RUNSLOW`/`RUNWEEKLY` make-var quirk)
- Generic pytest CLI usage (`-x`, `-k`, `--pdb`, fixtures, parametrize) — not vivarium-specific
- Framework console scripts (`simulate`, `psimulate`, etc.) → `framework-clis`

## Running tests in a vivarium repo

Two paths.

**`make test-*` — canonical, coverage baked in.**

```
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

**Direct `pytest` — narrow scope, opt-out of coverage.**

```
pytest tests/path/to/test_foo.py::test_bar -xvs
```

Use when iterating on a single failure, or when you don't want a coverage report on every run.

## Markers (`slow`, `cluster`, `weekly`)

`vivarium_testing_utils` auto-loads as a pytest11 plugin (no conftest import needed) and registers three markers with default-skip rules. Source: [`pytest_plugin.py`](file:///home/pnast/repos/vivarium_testing_utils/src/vivarium_testing_utils/pytest_plugin.py).

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

## Expanding scope conversationally

Start narrow when iterating on a single failure; broaden as confidence grows.

1. `pytest tests/path/to/test_foo.py::test_bar -xvs` — one test, fail-fast, verbose, no capture.
2. `pytest tests/path/to/test_foo.py -x` — the whole file.
3. `pytest tests/path/to/ -x` — the directory.
4. `make test-unit` (or `test-integration`, `test-e2e`) — the whole layer, with coverage. Only where the subdir exists.
5. `make test-all` — everything fast (skips `@slow`, `@weekly`, `@cluster` per the rules above).
6. `make test-all RUNSLOW=true` — adds `@slow`.
7. On Sunday, or with `RUNWEEKLY=true`, on the cluster: `make test-all RUNSLOW=true RUNWEEKLY=true` — adds `@weekly` and (if `sbatch` is on PATH) `@cluster`.

## Coverage

Coverage is **automatic** in every `make test-*` target — there is no `make test-no-coverage`. To skip coverage, drop down to direct `pytest`.

- Terminal report: `--cov-report term` prints during the run.
- HTML report: `./output/htmlcov_<type>/index.html` (e.g. `htmlcov_unit`, `htmlcov_tests` for `test-all`).
- Per-target `.coverage` DB: `./output/.coverage.<type>` (or `./output/.coverage` for `test-all`).
- Configured per-package under `[tool.coverage.*]` in `pyproject.toml` — usually just `source = ["<pkg>"]` and `show_missing = true`. No threshold gates anywhere in the ecosystem; the report is informational.

## When behavior surprises you

Two stable sources:
- `/home/pnast/repos/vivarium_testing_utils/src/vivarium_testing_utils/pytest_plugin.py` — marker registration, skip hooks, `SLOW_TEST_DAY`, xdist worker calc, the `no_gbd_cache` fixture.
- `/home/pnast/repos/vivarium_build_utils/resources/makefiles/test.mk` — exactly what each `make test-*` target runs.
