# TDD Plan: `dagger restart` subcommand

## Goal
Add a `restart` subcommand to the `dagger` CLI group, analogous to `psimulate restart`: it takes a positional results directory from a previous `dagger run`, reloads the saved workflow configuration, and resumes the pre-existing Jobmon workflow (skipping completed tasks). The `--resume` flag on `dagger run` is removed — `restart` replaces it.

## Acceptance criteria
- `dagger restart RESULTS_DIRECTORY` resumes a previously-started workflow from its output directory, reusing the saved config and the same Jobmon workflow identity, skipping completed tasks.
- `RESULTS_DIRECTORY` is a **positional** argument; restart reads `configuration.yaml` and `.workflow_args` from it (no `-c`/`-o`).
- A fresh `dagger run` always starts a new workflow; the `--resume` flag no longer exists.
- `restart` accepts `--project/-P`, `--queue/-q`, `--max-attempts/-m` overrides (like `psimulate restart`), applied over the saved config.
- Restart forces the workflow's `output_directory` to `RESULTS_DIRECTORY` and notifies Slack with a `"dagger restart"` label; it errors clearly if the directory isn't a resumable `dagger run` output.

## Affected modules
| Source | Tests |
|---|---|
| `src/vivarium_cluster_tools/dagger/cli.py` | `tests/dagger/test_cli.py` |
| `src/vivarium_cluster_tools/dagger/runner.py` | `tests/dagger/test_runner.py` |
| `src/vivarium_cluster_tools/dagger/config/utilities.py` (add `CONFIGURATION_FILENAME` constant) | — |

---

## Nuances & gotchas vs. `psimulate restart`

These are the things that differ from the psimulate analogue and are easy to get wrong. They're the reason for several of the tests below.

1. **Config source is a *saved file*, not a reconstructed spec.** `psimulate restart` rebuilds the model spec from the results dir via `build_model_specification`. `dagger restart` instead reloads the `configuration.yaml` that `dagger run` wrote (`runner._write_workflow_configuration`). **This makes the run→write→restart→read round-trip load-bearing**: if `workflow_config_to_dict` (serialize) and `WorkflowConfig.parse_yaml_file` + `load_workflow_config` (parse) aren't symmetric, restart silently runs a *different* workflow than the original. → dedicated round-trip test.

2. **`.build_timestamp` matters only for workflows with a simulation step.** Verified in the code: `get_or_create_build_timestamp` is called from exactly one place — the **simulation-step** builder (`interface.py` `get_simulation_step_tasks`, line ~174) — to reproduce psimulate's `model_name/timestamp` output layout so a resumed sim finds its prior outputs. The bash/pytest/python/notebook builders receive `is_resume` but never touch the timestamp.
   - `.workflow_args` (`WORKFLOW_ARGS_FILENAME`) — the Jobmon workflow identity; reusing it is what makes Jobmon resume the *same* workflow. **Always required** for restart, regardless of step types.
   - `.build_timestamp` (`BUILD_TIMESTAMP_FILENAME`) — **only relevant if the workflow has a simulation step.** For such workflows it must survive into restart (the builder auto-reuses it if present; if missing, the sim's outputs relocate to a fresh `model_name/timestamp` subdir and resume won't find them). For sim-free workflows it's never created and is irrelevant. → restart's validation should hard-require `.workflow_args`, but treat `.build_timestamp` as optional (its presence/absence is driven by whether a sim step exists).

3. **`.workflow_args` may not exist if the original run died early.** In current `run_workflow`, `.workflow_args` is written *after* `build_workflow_from_config` (runner.py line ~72). A run that crashes during build never persists it, so restart can't resume. `psimulate restart` is more forgiving (it discovers completed outputs). **Recommended fix as part of this work:** persist `.workflow_args` *before* building/binding so an early failure is still restartable. Restart must also raise a clear, actionable error when the file is absent.

4. **No `-c`/`--config` and no `-o`/`--output-directory` on restart.** The positional results dir *replaces* `-c`, and *is* the output dir. Restart must also **override** the saved config's `output_directory` field with the actual results dir (in case the dir was moved/copied after the original run).

5. **Positional-only — no deprecated `--results-root`.** `psimulate restart` carries a deprecated `--results-root/-R` option (via `resolve_deprecated_positional`) for legacy callers. `dagger restart` is brand-new, so it's positional-only — no deprecated option, no `resolve_deprecated_positional`.

6. **Removing `--resume` is a clean break — fine here.** This epic branch is **unreleased**, so removing the `--resume` flag from `dagger run` outright (no deprecation cycle) is acceptable — dagger isn't live yet. The resume code path (reading `.workflow_args`) *moves* from `run_workflow` into the restart path; `run_workflow` always generates fresh args. Still worth a one-line CHANGELOG note under the unreleased `3.2.0` for the record.

7. **Slack label.** `run` uses `command_label="dagger run"`; restart must use `"dagger restart"` so notifications are distinguishable.

8. **Idempotent re-restart.** Restarting twice should be safe (Jobmon resume + same `.workflow_args` is idempotent). Covered by e2e, not unit (see Phase 3 follow-ups).

### Overrides (decided): `--project`, `--queue`, `--max-attempts`
Mirroring `psimulate restart`, `dagger restart` accepts `--project/-P`, `--queue/-q`, and `--max-attempts/-m`, forwarded as overrides to `load_workflow_config(...)` (which already merges CLI overrides over the saved YAML). `output_directory` is still **forced** to the positional results dir; `-c`/`-o` and the other `run` overrides (`--name`, `--default-environment`) are intentionally **not** on restart.

---

## Phase 1: xfail tests + stubs

### Source stubs

**`src/vivarium_cluster_tools/dagger/config/utilities.py`** — add the filename constant (so both the writer and restart reader share it):

```python
CONFIGURATION_FILENAME = "configuration.yaml"
"""File written to the output directory holding the full workflow config,
reloaded by ``dagger restart``."""
```

**`src/vivarium_cluster_tools/dagger/runner.py`** — add:

```python
def restart_workflow(
    results_directory: Path,
    *,
    project: str | None = None,
    queue: str | None = None,
    max_attempts: int | None = None,
    verbose: int = 0,
) -> None:
    """[stub] Implement in Phase 2."""
    raise NotImplementedError
```

(Do **not** add the `restart` CLI command or remove `--resume` yet — that wiring happens in Phase 2, which is what flips the CLI tests from XFAIL to XPASS.)

### Tests

**`tests/dagger/test_runner.py`** — add (module already has `pytest.importorskip("jobmon")` at top; mock the jobmon/network *boundaries* — `build_workflow_from_config`, `client.bind_and_run_workflow`, `send_slack_notification` — and use real files under `tmp_path`):

```python
import pytest

_BUILD = "vivarium_cluster_tools.dagger.runner.build_workflow_from_config"
_BIND = "vivarium_cluster_tools.dagger.runner.client.bind_and_run_workflow"
_SLACK = "vivarium_cluster_tools.dagger.runner.send_slack_notification"


def _seed_run_output(tmp_path, workflow_dict) -> Path:
    """Arrange a results dir as `dagger run` would leave it: configuration.yaml + .workflow_args."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "configuration.yaml").write_text(yaml.dump({"workflow": workflow_dict}))
    (results / ".workflow_args").write_text("workflow_test_abc123_20260601_000000")
    (results / ".build_timestamp").write_text("2026_06_01_00_00_00")
    return results


@pytest.mark.xfail(reason="not implemented: restart_workflow")
def test_restart_loads_saved_configuration(tmp_path) -> None:
    """restart reads configuration.yaml from the results dir and builds from it."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results)))
    with patch(_BUILD) as build, patch(_BIND, return_value=("D", "url")), patch(_SLACK):
        restart_workflow(results)
    assert build.call_args.args[0].name == "test_workflow"


@pytest.mark.xfail(reason="not implemented: persisted workflow_args reuse")
def test_restart_reuses_persisted_workflow_args(tmp_path) -> None:
    """restart reads .workflow_args and passes it as build_workflow_from_config(workflow_args=...)."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results)))
    with patch(_BUILD) as build, patch(_BIND, return_value=("D", "url")), patch(_SLACK):
        restart_workflow(results)
    assert build.call_args.kwargs["workflow_args"] == "workflow_test_abc123_20260601_000000"


@pytest.mark.xfail(reason="not implemented: resume=True on bind")
def test_restart_resumes_jobmon_workflow(tmp_path) -> None:
    """restart calls bind_and_run_workflow with resume=True."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results)))
    with patch(_BUILD), patch(_BIND, return_value=("D", "url")) as bind, patch(_SLACK):
        restart_workflow(results)
    assert bind.call_args.kwargs["resume"] is True


@pytest.mark.xfail(reason="not implemented: output_directory override")
def test_restart_forces_output_directory_to_results_dir(tmp_path) -> None:
    """Even if the saved config points elsewhere, restart uses the given results dir."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory="/stale/path"))
    with patch(_BUILD) as build, patch(_BIND, return_value=("D", "url")), patch(_SLACK):
        restart_workflow(results)
    assert build.call_args.args[0].output_directory == results


@pytest.mark.xfail(reason="not implemented: slack label")
def test_restart_notifies_with_restart_label(tmp_path) -> None:
    """restart sends a Slack notification labelled 'dagger restart'."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results)))
    with patch(_BUILD), patch(_BIND, return_value=("D", "url")), patch(_SLACK) as slack:
        restart_workflow(results)
    assert slack.call_args.kwargs["command_label"] == "dagger restart"


@pytest.mark.xfail(reason="not implemented: missing .workflow_args guard")
def test_restart_missing_workflow_args_errors(tmp_path) -> None:
    """A results dir without .workflow_args raises a clear error (not resumable)."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results)))
    (results / ".workflow_args").unlink()
    with patch(_BUILD), patch(_BIND), patch(_SLACK):
        with pytest.raises(FileNotFoundError, match="workflow_args"):
            restart_workflow(results)


@pytest.mark.xfail(reason="not implemented: restart applies overrides over saved config")
def test_restart_applies_project_override(tmp_path) -> None:
    """An override (e.g. project) is merged over the saved config before building."""
    results = _seed_run_output(tmp_path, _workflow_dict(output_directory=str(results), project="proj_old"))
    with patch(_BUILD) as build, patch(_BIND, return_value=("D", "url")), patch(_SLACK):
        restart_workflow(results, project="proj_new")
    assert build.call_args.args[0].project == "proj_new"


@pytest.mark.xfail(reason="not implemented: run/restart config round-trip")
def test_run_then_restart_roundtrip(tmp_path) -> None:
    """A config written by run_workflow loads back identically for restart_workflow."""
    cfg = _workflow_config(output_directory=tmp_path / "results")  # real WorkflowConfig
    with patch(_BUILD), patch(_BIND, return_value=("D", "url")), patch(_SLACK):
        run_workflow(cfg)                # writes configuration.yaml + .workflow_args
        restart_workflow(tmp_path / "results")  # must not raise; loads the saved config
    # Assert restart built from an equivalent config (same name + step count)
    ...
```

**`tests/dagger/test_cli.py`** — add (module already has `pytest.importorskip("jobmon")`; mirror the existing `_WORKFLOW_MAIN` mock style):

```python
_RESTART_MAIN = "vivarium_cluster_tools.dagger.cli.runner.restart_workflow"


@pytest.mark.xfail(reason="not implemented: restart subcommand")
def test_restart_dispatches_results_directory(tmp_path) -> None:
    """`dagger restart <dir>` calls runner.restart_workflow with the resolved dir."""
    results = tmp_path / "results"; results.mkdir()
    with patch(_RESTART_MAIN) as mock_main:
        result = CliRunner().invoke(dagger, ["restart", str(results)])
    assert result.exit_code == 0, result.output
    assert mock_main.call_args.kwargs["results_directory"] == results.resolve()


@pytest.mark.xfail(reason="not implemented: restart positional required")
def test_restart_requires_results_directory() -> None:
    """`dagger restart` with no positional is a usage error."""
    result = CliRunner().invoke(dagger, ["restart"])
    assert result.exit_code != 0
    assert "missing" in result.output.lower() or "required" in result.output.lower()


@pytest.mark.xfail(reason="not implemented: restart dir must exist")
def test_restart_nonexistent_directory_errors(tmp_path) -> None:
    """`dagger restart <missing>` is rejected by Click (exists=True)."""
    result = CliRunner().invoke(dagger, ["restart", str(tmp_path / "nope")])
    assert result.exit_code != 0
    assert "does not exist" in result.output.lower()


@pytest.mark.xfail(reason="not implemented: restart overrides")
def test_restart_passes_overrides(tmp_path) -> None:
    """`dagger restart <dir> -P proj -q long.q -m 5` forwards overrides to restart_workflow."""
    results = tmp_path / "results"; results.mkdir()
    with patch(_RESTART_MAIN) as mock_main:
        result = CliRunner().invoke(
            dagger, ["restart", str(results), "-P", "proj_x", "-q", "long.q", "-m", "5"]
        )
    assert result.exit_code == 0, result.output
    kw = mock_main.call_args.kwargs
    assert (kw["project"], kw["queue"], kw["max_attempts"]) == ("proj_x", "long.q", 5)


@pytest.mark.xfail(reason="not implemented: --resume removed from run")
def test_run_no_longer_accepts_resume(tmp_path) -> None:
    """The removed `--resume` flag is now an unknown option on `dagger run`."""
    workflow_yaml = _write_yaml(tmp_path, _make_workflow_dict(tmp_path))
    result = CliRunner().invoke(dagger, ["run", "--config", str(workflow_yaml), "--resume"])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()
```

### After Phase 1
`pytest tests/dagger/test_runner.py tests/dagger/test_cli.py` reports the new tests as **XFAIL** (runner tests hit `NotImplementedError`; CLI tests fail because `restart` doesn't exist yet and `--resume` is still present), no collection errors, suite green.

---

## Phase 2: implementation

Implement in this order; after each, the named test flips to **XPASS**.

1. **`test_restart_loads_saved_configuration`** — in `restart_workflow`, load `results_directory / CONFIGURATION_FILENAME` via `load_workflow_config(...)`; build via `build_workflow_from_config`. (Use the new `CONFIGURATION_FILENAME` constant in both `_write_workflow_configuration` and here — DRY the filename.)
2. **`test_restart_reuses_persisted_workflow_args`** — read `results_directory / WORKFLOW_ARGS_FILENAME` and pass it as `workflow_args=` to `build_workflow_from_config`.
3. **`test_restart_resumes_jobmon_workflow`** — call `client.bind_and_run_workflow(..., resume=True, ...)`.
4. **`test_restart_forces_output_directory_to_results_dir`** — pass `output_directory=results_directory` into `load_workflow_config` (its CLI-override arg) so the saved value is overridden.
4b. **`test_restart_applies_project_override`** — thread `project`/`queue`/`max_attempts` from `restart_workflow`'s params into the same `load_workflow_config(...)` override args (it already merges CLI overrides over the saved YAML).
5. **`test_restart_notifies_with_restart_label`** — call `send_slack_notification(..., command_label="dagger restart", ...)`. Best done by extracting a shared `_execute_workflow(workflow_config, *, workflow_args, resume, command_label)` helper from `run_workflow`; `run_workflow` calls it with fresh args/`resume=False`/`"dagger run"`, `restart_workflow` with persisted args/`resume=True`/`"dagger restart"`.
6. **`test_restart_missing_workflow_args_errors`** — guard the `.workflow_args` read with a clear `FileNotFoundError` ("…not a resumable dagger run output: missing .workflow_args"). Also move the `.workflow_args` write earlier in `_execute_workflow` (before build/bind) per gotcha #3.
7. **`test_run_then_restart_roundtrip`** — falls out once 1–6 are done; confirms serialize/parse symmetry. If it fails, fix `serialization.workflow_config_to_dict` ↔ `parsing` asymmetry (the real bug this test guards).
8. **CLI tests (`test_restart_*`, `test_restart_passes_overrides`, `test_run_no_longer_accepts_resume`)** — add the `restart` command to `dagger/cli.py`:
   - positional `results_directory`: `click.Path(exists=True, file_okay=False, writable=True)` + `coerce_to_full_path`;
   - override options `--project/-P`, `--queue/-q`, `--max-attempts/-m` (reuse the same option definitions/`IntRange` as `run`); `with_verbose_and_pdb`;
   - dispatch through `handle_exceptions(runner.restart_workflow, ...)`, forwarding `project`/`queue`/`max_attempts`/`verbose`.
   Then remove the `--resume` option, the `resume` param, and the `resume=` arg from `run`/`run_workflow`.

### After Phase 2
All Phase 1 tests show **XPASS**.

---

## Phase 3: cleanup
1. Remove `@pytest.mark.xfail(...)` from each new test in both test modules.
2. Run `cd libs/cluster-tools && make check` — all PASS, mypy clean, docs build clean.
3. Add a `CHANGELOG.rst` entry under `3.2.0`: *"Add `dagger restart` subcommand; remove `--resume` flag from `dagger run`."*
4. Update the `dagger` group docstring / `run` docstring to mention `restart` and drop the `--resume` mention.

### Optional refactor opportunities
- The `_execute_workflow` extraction (step 5) is the main de-duplication and is part of Phase 2, not optional.
- Consider a tiny `_load_resumable_output(results_directory) -> tuple[WorkflowConfig, str]` helper if reading config + args + validation makes `restart_workflow` long. Only if it reads cleanly — otherwise leave inline.

---

## Follow-ups (not Phase 1 reds)
- **e2e/cluster test** in `tests/dagger/test_e2e.py` (`@pytest.mark.cluster`): real `dagger run` that's killed mid-flight, then `dagger restart` resumes to completion and skips done tasks. Owns its SLURM lifecycle; out of scope for unit xfail TDD.
- Confirm the **overrides decision** above; if yes, additive CLI options + tests.

## Completion checklist
- [ ] Phase 1 committed: stubs + xfail tests, suite green with the new tests XFAIL
- [ ] Phase 2 committed: implementation done, all new tests XPASS
- [ ] Phase 3 committed: xfail markers removed, all tests PASS
- [ ] `--resume` removed from `dagger run`; CHANGELOG + docstrings updated
- [ ] `make check` clean (lint, mypy, tests, docs)
