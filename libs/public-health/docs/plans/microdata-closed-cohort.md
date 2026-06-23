# TDD Plan: MicrodataObserver closed-cohort mode (`single_random_sample`)

## Goal
Add a `single_random_sample` config flag to `MicrodataObserver`. When `True`, the observer
draws a fixed cohort **once** at the start of the simulation and records only those simulants at
every subsequent observed timestep (a *closed cohort*), instead of drawing a fresh random sample
each step (the existing `row_limit` behavior).

## Acceptance criteria
- `single_random_sample` defaults to `False`; existing fresh-sample behavior is unchanged.
- `single_random_sample=True` with `row_limit=None` raises `ResultsConfigurationError` at setup
  (the two options must be set together, intentionally).
- With `single_random_sample=True`, the **same** simulants are recorded at every observed
  timestep — the closed cohort — sized `row_limit // n_observed_timesteps`.
- The cohort is fixed to the initial population (mid-sim entrants never join it), and the filter
  is re-applied each step: a cohort member that leaves the filter (or the sim) is dropped and
  never refilled, so `row_limit` stays an upper bound.

## Environment
Run pytest in the **`vph`** conda env — this feature depends on the engine
`conccating-observation-custom-gatherer` branch (the `results_gatherer` param on
`register_concatenating_observation`), installed editable into `vph` from the worktree at
`~/hooks/vse-engine-hook/libs/engine`. The in-tree `libs/engine` does **not** have that param.

```bash
~/miniconda3/envs/vph/bin/pytest libs/public-health/tests/results/test_microdata.py -xvs
```

## Affected modules
| Source | Tests |
|---|---|
| `libs/public-health/src/vivarium/public_health/results/microdata.py` | `libs/public-health/tests/results/test_microdata.py` |

## Design decisions (settled)
- **Requires `row_limit`.** `single_random_sample=True` + `row_limit=None` → raise at setup.
- **Cohort size** = `row_limit // n_observed_timesteps` = the existing `self.max_rows_per_timestep`.
  Reuses the existing "row_limit < n_observed_timesteps → raise" path unchanged.
- **Initial population only.** Cohort captured on the *first* population-initializer call; later
  `on_initialize_simulants` calls (mid-sim entrants) are ignored via a `None`-guard.
- **Filter re-applied each step.** The framework hands the custom `results_gatherer` the
  *already-filtered* population, so `pop.loc[pop.index.intersection(self.cohort)]` drops members
  that no longer match the filter or have left the sim. No refill.
- **⚠ Flagged:** the cohort is sampled from the **full** initial population, not the
  filter-narrowed one — `row_limit` is treated as a strict upper bound. Revisit if baseline
  (filter-defined) cohort selection is wanted instead.

---

## Phase 1: xfail tests + stubs

### Source stub

The only change needed for the new tests to *run* (rather than `KeyError` on config access) is
the new config default. The private methods are added in Phase 2 — they're not referenced by name
in the tests, so no raising stubs are required, and existing tests stay green because the new code
paths only activate when `single_random_sample=True`.

**`microdata.py`** — add to `configuration_defaults`:

```python
config[self.name] = {
    "columns": [],
    "filter": [],
    "timesteps": [],
    "row_limit": None,
    "single_random_sample": False,
}
```

### Tests

**`test_microdata.py`** — add (in this order):

```python
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.event import Event
# (Component, Event, SimulantData, Builder already imported at top of file)


@pytest.mark.xfail(reason="not implemented: single_random_sample requires row_limit")
def test_microdata_observer_single_random_sample_requires_row_limit(
    base_config, base_plugins
) -> None:
    """single_random_sample without a row_limit raises a configuration error at setup."""
    config = _configure(
        base_config,
        {"columns": ["age"], "single_random_sample": True},  # no row_limit
    )
    with pytest.raises(ResultsConfigurationError, match="single_random_sample"):
        InteractiveContext(
            components=[BasePopulation(), MicrodataObserver()],
            configuration=config,
            plugin_configuration=base_plugins,
        )


@pytest.mark.xfail(reason="not implemented: single_random_sample closed cohort")
def test_microdata_observer_single_random_sample_records_fixed_cohort(
    base_config, base_plugins
) -> None:
    """single_random_sample records the same once-sampled cohort at every observed step."""
    config = _configure(
        base_config,
        {
            "columns": ["simulant_id"],
            "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],  # 2 observed -> 200 // 2 = 100
            "row_limit": 200,
            "single_random_sample": True,
        },
        population_size=1000,
    )
    sim = InteractiveContext(
        components=[BasePopulation(), _SimulantID(), MicrodataObserver()],
        configuration=config,
        plugin_configuration=base_plugins,
    )

    sim.step()  # FIRST_EVENT_TIME -> observed
    sim.step()  # SECOND_EVENT_TIME -> observed
    result = sim.get_results()["microdata_observer"]

    cohorts = result.groupby("event_time")["simulant_id"].apply(set)
    assert all(len(cohort) == 100 for cohort in cohorts)
    # The SAME simulants both steps - a fixed closed cohort, unlike the fresh-sample mode.
    assert cohorts.iloc[0] == cohorts.iloc[1]


@pytest.mark.xfail(reason="not implemented: closed cohort drops members leaving the filter")
def test_microdata_observer_single_random_sample_drops_members_leaving_filter(
    base_config, base_plugins
) -> None:
    """A cohort member that leaves the filter is dropped and never refilled (upper bound)."""
    config = _configure(
        base_config,
        {
            "columns": ["simulant_id"],
            "filter": ["eligible == True"],
            "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
            "row_limit": 200,  # cohort size 100
            "single_random_sample": True,
        },
        population_size=1000,
    )
    sim = InteractiveContext(
        components=[BasePopulation(), _SimulantID(), _Disqualifier(), MicrodataObserver()],
        configuration=config,
        plugin_configuration=base_plugins,
    )

    sim.step()  # FIRST_EVENT_TIME -> full cohort still eligible
    sim.step()  # _Disqualifier flips half ineligible, then SECOND_EVENT_TIME observes
    result = sim.get_results()["microdata_observer"]

    cohorts = result.groupby("event_time")["simulant_id"].apply(set)
    first, second = cohorts.iloc[0], cohorts.iloc[1]
    assert len(first) == 100
    assert second < first  # strict subset: members left, none were added back
```

Add this test-only helper near `_SimulantID` (mirrors its `register_initializer` pattern; its
`population_view` and `on_time_step` listener are wired automatically by `Component`):

```python
class _Disqualifier(Component):
    """Mark all simulants eligible, then disqualify half of them before the second step."""

    def setup(self, builder: Builder) -> None:
        self._steps = 0
        builder.population.register_initializer(
            initializer=self._initialize, columns=["eligible"]
        )

    def _initialize(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.Series(True, index=pop_data.index, name="eligible")
        )

    def on_time_step(self, event: Event) -> None:
        self._steps += 1
        if self._steps == 2:  # after the first observed step, before the second
            pop = self.population_view.get(event.index)
            pop.loc[pop.index[::2], "eligible"] = False
            self.population_view.update(pop)
```

### After Phase 1
`pytest libs/public-health/tests/results/test_microdata.py` reports **3 XFAIL**, no errors, and
all pre-existing tests still PASS.

---

## Phase 2: implementation

Edit `microdata.py`. Map 1:1 to the Phase 1 tests, simplest first.

1. **`…requires_row_limit`** — in `register_observations`, after reading `config`, add the
   validation: if `config.single_random_sample and config.row_limit is None`, raise
   `ResultsConfigurationError` whose message contains `single_random_sample` (state that it
   requires `row_limit`). Rationale: a closed cohort has no defined size without `row_limit`.

2. **`…records_fixed_cohort`** — wire the cohort path:
   - In `setup`, register the initializer: `builder.population.register_initializer(self._sample_cohort, columns=None)` and init `self.cohort: pd.Index | None = None`. (`columns=None` = no private columns; supported.)
   - Add `_sample_cohort(self, pop_data: SimulantData) -> None`: on the first call only
     (`if self.cohort is None:`), draw `self.randomness.get_draw(pop_data.index,
     additional_key="cohort_selection")` and set `self.cohort = draws.nlargest(self.max_rows_per_timestep).index`. Guard fixes the cohort to the initial population (Q2).
   - Add `_cohort_rows(self, pop: pd.DataFrame) -> pd.DataFrame`:
     `return pop.loc[pop.index.intersection(self.cohort)]`.
   - In `register_observations`, inside the `if config.row_limit is not None:` block, choose the
     gatherer: `self._cohort_rows` when `config.single_random_sample` else `self._sample_rows`.
   Rationale: cohort size reuses `max_rows_per_timestep` (already computed in that block), so the
   existing `row_limit < n_observed_timesteps` guard covers the cohort case for free.

3. **`…drops_members_leaving_filter`** — no new code: the framework filters the population before
   calling `_cohort_rows`, so `index.intersection(self.cohort)` already drops members who left the
   filter, with no refill. This test verifies that emergent behavior. (If it doesn't XPASS after
   step 2, the gatherer is filtering in the wrong order — re-check.)

After each step run `pytest …::<test_name>` and confirm XPASS.

### After Phase 2
All 3 Phase 1 tests show XPASS.

---

## Phase 3: cleanup

1. Remove `@pytest.mark.xfail(...)` from the 3 new tests.
2. `import pytest` stays (other tests use it).
3. Run `~/miniconda3/envs/vph/bin/pytest libs/public-health/tests/results/test_microdata.py` —
   all PASS.
4. Update the class docstring `Configuration` section in `microdata.py` to document
   `single_random_sample` (default `False`; requires `row_limit`; closed cohort sampled once from
   the initial population, filter re-applied each step).

### Optional refactor opportunities
- None planned — the cohort path reuses `max_rows_per_timestep` and the existing error path; the
  added methods are small and direct.

---

## Follow-ups (not in this plan)
- **Mid-sim entrants (Q2) integration test.** Verifying that simulants *born* during the sim never
  join the cohort needs a fertility/simulant-creator component — heavier than a unit test. Track
  separately if coverage is wanted.
- **Flagged decision.** If baseline (filter-narrowed) cohort selection is preferred over
  full-population sampling, revisit `_sample_cohort` and add a test asserting the cohort is drawn
  only from filter-matching simulants.

## Completion checklist
- [ ] Phase 1 committed: config default + 3 xfail tests + `_Disqualifier` helper; suite green with 3 XFAIL
- [ ] Phase 2 committed: validation + `_sample_cohort` + `_cohort_rows` + gatherer wiring; 3 XPASS
- [ ] Phase 3 committed: markers removed, docstring updated, all PASS
- [ ] Mypy clean (`cd libs/public-health && make mypy`)
- [ ] Existing `test_microdata.py` suite still passes in `vph`
