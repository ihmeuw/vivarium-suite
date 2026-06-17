# TDD Plan: Microdata Observer

## Goal
Add a **microdata observer** (`MicrodataObserver`) that records a **user-specified set of columns**
for each simulant at each observed timestep, concatenated across timesteps, with optional
row/timestep filtering and a row cap. It is a black-box, configuration-driven observer: drop it into
a simulation, name the columns, and it "just works" without per-model code.

## Status & design changes
- **2026-06-17 — column-driven, not "all attributes."** It records the columns the user lists (for
  all/filtered simulants), like a configurable `ConcatenatingObservation` — *not* every attribute.
  The old "give me everything" design (`requires_all_attributes`, `get_all_attribute_names`,
  gather-time attribute resolution, mapped-column exclusion) is gone.
- **2026-06-17 — relocate to `public-health`, compose generic engine primitives.** *(Pending team
  confirmation.)* PR review on Task 1 raised two points we agree with: (a) a concrete observer
  belongs with the other concrete observers in `vivarium-public-health`, not in the generic
  `vivarium-engine` framework; and (b) it should **not** require microdata-specific machinery
  (`MicrodataObservation`, `register_microdata_observation`) in engine. So the observer moves to
  public-health and is built by composing the *generic* engine results primitives. See
  "Port from engine" below.

## Acceptance criteria
- A user adds `MicrodataObserver` (from public-health) to any simulation, names a set of `columns`,
  and it records exactly those columns (plus `event_time`) for every simulant at every observed
  timestep, concatenated across steps.
- Task 1 = column recording; Task 2 = row filter, timestep filter, row cap (random sample);
  Task 3 = the closed-cohort / `single_random_sample` option.
- Each config option is exercised by a focused, readable test that locks the recorded contract
  (columns and rows) without over-specifying.
- No microdata-specific code lives in `vivarium-engine`.

## Architecture (target)
`MicrodataObserver` lives in `vivarium-public-health` and composes the **generic** engine results
interface — no `MicrodataObservation` subclass and no `register_microdata_observation` method in
engine. In `register_observations` it calls the existing
`builder.results.register_unstratified_observation(...)` with:
- `requires_attributes = ["event_time", *columns]`
- `results_gatherer` — select the configured columns and apply the row-cap random sample
- `results_updater = ConcatenatingObservation.concatenate_results` — reuse engine's concat (a
  `@staticmethod`), no re-implementation
- `pop_filter` — the AND-joined `filter` query; `to_observe` — the timestep predicate

Why this shape: engine's results interface is deliberately domain-agnostic (generic
`register_*_observation` methods); concrete observers (disease, disability, mortality, …) live in
public-health and compose those. The microdata observer follows that pattern. The only logic that
lands in the observer is the gatherer (column-select + cap), which is legitimately its own concern;
concatenation is reused from engine.

## Open design decisions
1. **Home: `public-health`.** ⚠️ *Pending team confirmation.* Caveat to raise: `MicrodataObserver`
   is domain-agnostic (records arbitrary columns), so "public-health" is a slight misnomer — but
   public-health is the de-facto home for reusable concrete observers. Confirm there isn't a better
   home for a generic observer.
2. **No microdata-specific engine code.** ⚠️ *Pending.* Compose `register_unstratified_observation`
   + callables (leaning choice; zero engine change). Alternative if the team wants the cap reusable
   framework-wide: expose a *generic* `register_observation(observation_type=...)` hook in engine
   and keep a `MicrodataObservation` subclass in public-health. Decide which.
3. **Name & config key.** ✅ Class `MicrodataObserver` (one word "microdata"), snake-cased name
   `microdata_observer`; config block + result/output keyed on the **full name**
   (`microdata_observer`), matching the mockup and supporting custom-named instances
   (`alzheimers_microdata_observer:`).
4. **Empty/unspecified `columns`.** ✅ Raise `ResultsConfigurationError` at setup — an unconfigured
   microdata observer is almost certainly a mistake; a clear error beats a silent empty file.
5. **Memory columns (`previous_timestep_columns`, `initial_columns`).** ❌ Not supported — out of
   scope.
6. **`event_time`.** Always recorded (the observer adds it to `requires_attributes`), regardless of
   `columns`.

## Affected modules (target)
| Source | Tests |
|---|---|
| `libs/public-health/src/vivarium/public_health/results/microdata.py` *(new)* | `libs/public-health/tests/.../results/test_microdata.py` *(new)* |
| `libs/public-health/CHANGELOG.rst` (entry) | — |

Engine: **remove** the microdata-specific additions (see Port). No new engine code under the
leaning choice; the alternative adds only a generic `register_observation` hook.

## Port from engine (current PRs #132 / #133)
Tasks 1–2 are currently implemented *in engine* on two stacked PRs. Relocating means:

**Remove from engine:**
- `MicrodataObservation` (`results/observation.py`) and `register_microdata_observation`
  (`results/interface.py`).
- the microdata tests in `tests/framework/results/test_observer.py` (the `Observer` ABC tests stay).
- the "Add Microdata Observer" entry in `libs/engine/CHANGELOG.rst`.

**Add to public-health:**
- `MicrodataObserver` in `results/microdata.py`, reworked to use `register_unstratified_observation`
  + the gatherer/updater described in Architecture.
- a `CHANGELOG.rst` entry in **public-health**.

**Rebuild tests on public-health scaffolding.** The engine tests use the `Hogwarts` helper, which
isn't importable from public-health (test packages aren't installed). The microdata tests must be
rebuilt on a public-health sim fixture (a base population + a component that creates recordable
columns), not copy-pasted.

**PR strategy:** #132 and #133 both touch the engine files being gutted. Decide whether to rework
those branches in place or close them and land the public-health version fresh, so one PR isn't
removing what the other adds.

---

# Task 1 (PR 1): Column recording

`MicrodataObserver` (public-health) records the configured `columns` (+ `event_time`) for every
simulant, concatenated across timesteps. Config read from `builder.configuration[self.name]` (the
`microdata_observer:` block). Task 1 key: `columns`.

**Implementation:** `register_observations` reads `columns` (raising `ResultsConfigurationError` if
empty) and calls `register_unstratified_observation` with `requires_attributes=["event_time",
*columns]`, a `results_gatherer` returning `pop[requires_attributes]`, and
`results_updater=ConcatenatingObservation.concatenate_results`.

**Tests (3, on a public-health sim fixture):** observer registers a single named observation;
records exactly the configured columns (+ `event_time`), one row per simulant per step, concatenated
across two steps with distinct `event_time`; empty `columns` raises `ResultsConfigurationError`.
3-phase xfail flow.

---

# Task 2 (PR 2): Row filter, timestep filter, row cap

Adds config keys `filter`, `timesteps`, `row_limit`.

- **Row filter (`filter`)** — list of Pandas query strings, AND-joined (`(a) and (b)`), passed as
  `pop_filter`. Empty → no filter.
- **Timestep filter (`timesteps`)** — a `to_observe` predicate matching `event.time` (the recorded
  `event_time`) against the configured dates. Empty → every timestep.
- **Row cap (`row_limit`)** — a per-timestep cap on row *count*. Compute
  `max_rows_per_timestep = row_limit // n_observed_timesteps` (`n_observed_timesteps` = `len(timesteps)`
  if configured, else estimated from the time config — an estimate is acceptable since `row_limit`
  is an upper bound). The capped rows are a **fresh random sample drawn each observed timestep** via
  a vivarium randomness stream (`get_stream(self.name).get_draw` + `nsmallest(max_rows_per_timestep)`
  — reproducible/CRN-consistent, **not** the first-N). This is the *open/resampled* cap; a fixed
  closed cohort is Task 3.
  - **Guard:** if `row_limit < n_observed_timesteps` (would floor to 0 rows/step), raise
    `ResultsConfigurationError` rather than silently recording nothing.

`row_limit` is a **total** budget; the per-timestep cap is `row_limit // <observed timesteps>`
(floored) — document this in the observer's config docstring.

**Tests (~5):** filter subsets simulants (AND-combined) and the result is non-empty + strictly
smaller than the population; only configured timesteps recorded; `row_limit` caps to
`row_limit // n_observed_timesteps` rows/step and draws a *different* sample each step (not first-N);
the no-`timesteps` estimate path is exercised; `row_limit < n_observed_timesteps` raises. 3-phase
xfail flow.

---

# Task 3 (PR 3): Closed cohort via static propensity (`single_random_sample`)

Builds on Task 2's cap. Task 2 caps the count with a fresh random sample each step (open/resampled);
Task 3 makes the cap select a **fixed, closed cohort** and adds the `single_random_sample` knob.

**Static propensity (assigned at initialization, in the Observer).** Register a
`microdata_propensity` state-table column filled once per simulant in `on_initialize_simulants`
(a `[0, 1)` draw, fixed for life, shared across microdata observers per the mockup) — the "static
propensity of being observed."

**`single_random_sample` (bool, default `True`).**
- `True` (default): closed cohort — observe the simulants whose static propensity falls below a
  fixed cutoff (`max_rows_per_timestep / population_size`); membership is stable, and a departed
  member is not backfilled (file ≤ `row_limit`).
- `False`: open/resampled — Task 2's fresh-random-sample-each-step behavior.

**Tests:** with a cap and `True`, the recorded cohort is a stable subset across steps (no backfill);
with `False`, the recorded set may change step to step. 3-phase xfail flow.

---

## Completion checklist (per task)
- [ ] Phase 1 committed: stubs + xfail tests, suite green with N XFAIL
- [ ] Phase 2 committed: implementations done, all new tests XPASS
- [ ] Phase 3 committed: xfail markers removed, all tests PASS
- [ ] `make mypy` clean (public-health ships `py.typed`)
- [ ] `make check` green in `libs/public-health`
- [ ] CHANGELOG entry added (public-health) or deferral noted in the PR
- [ ] No microdata-specific code remains in `libs/engine`

## Follow-ups (out of scope)
- Shared/static "propensity of being observed" across multiple microdata observers (requires a
  shared column owner rather than per-observer registration).
- File-output formatting/partitioning for the recorded microdata (if not handled by the standard
  results writer).
