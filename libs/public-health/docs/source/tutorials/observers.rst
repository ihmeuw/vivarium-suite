=========
Observers
=========

This tutorial serves two purposes: it gives minimal working examples of
each public health observer, and it demonstrates three ways to configure
stratification.

These observer classes are public-health-specific helpers built on top of the
vivarium framework's
:class:`~vivarium.engine.framework.results.observer.Observer` base class (see the
`vivarium results concepts <https://vivarium-engine.readthedocs.io/en/latest/concepts/results.html>`_
documentation for details on the underlying results system).

.. contents::
   :local:
   :depth: 2

.. testsetup:: *

   import numpy as np
   import pandas as pd
   from loguru import logger
   logger.disable("vivarium")
   from vivarium.engine import Component, InteractiveContext
   from vivarium.engine.framework.engine import Builder
   from vivarium.public_health.disease import (
       DiseaseModel, DiseaseState, SusceptibleState, SI, SIS,
   )
   from vivarium.public_health.population import BasePopulation
   from vivarium.public_health.results import (
       DiseaseObserver, MortalityObserver, DisabilityObserver,
       CategoricalRiskObserver, MicrodataObserver, ResultsStratifier,
   )
   from vivarium.public_health.risks import Risk
   from vivarium.public_health._example_data import (
       BASE_PLUGINS, make_base_config, build_cause_table,
       disease_disability_weight,
   )
   base_plugins = BASE_PLUGINS


Common Setup
------------

.. testcode::

   from vivarium.engine import Component, InteractiveContext
   from vivarium.engine.framework.engine import Builder
   from vivarium.public_health.disease import (
       DiseaseModel, DiseaseState, SusceptibleState, SI, SIS,
   )
   from vivarium.public_health.population import BasePopulation
   from vivarium.public_health.results import (
       DiseaseObserver, MortalityObserver, DisabilityObserver,
       CategoricalRiskObserver, MicrodataObserver, ResultsStratifier,
   )
   from vivarium.public_health.risks import Risk
   from vivarium.public_health._example_data import (
       BASE_PLUGINS, make_base_config, build_cause_table,
       disease_disability_weight,
   )

   base_plugins = BASE_PLUGINS
   config = make_base_config()


DiseaseObserver
---------------

A :class:`~vivarium.public_health.results.DiseaseObserver` registers two
observations for a disease model:

- ``person_time_{disease}`` - person-years spent in each disease state,
  accumulated each time step. The ``sub_entity`` column contains the state
  name (e.g., ``"susceptible_to_test_cause"``, ``"test_cause"``).
- ``transition_count_{disease}`` - count of simulants transitioning between
  states each time step. The ``sub_entity`` column contains the transition
  name (e.g., ``"susceptible_to_test_cause_to_test_cause"``). Only
  transitions that actually occur appear in the output.

.. testcode::

   config = make_base_config()
   config.update({"population": {"population_size": 1000}}, layer="model_override")

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           SI("test_cause"),
           DiseaseObserver("test_cause"),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()
   results = sim.get_results()

   print(sorted(results.keys()))

.. testoutput::

   ['person_time_test_cause', 'transition_count_test_cause']

.. testcode::

   pt = results["person_time_test_cause"]
   print(pt.columns.tolist())
   print(pt["sub_entity"].tolist())

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'value']
   ['susceptible_to_test_cause', 'test_cause']

.. testcode::

   tc = results["transition_count_test_cause"]
   print(tc["measure"].iloc[0])
   print(tc["sub_entity"].iloc[0])
   assert tc["value"].iloc[0] > 0

.. testoutput::

   transition_count
   susceptible_to_test_cause_to_test_cause


MortalityObserver
-----------------

A :class:`~vivarium.public_health.results.MortalityObserver` registers two
observations, stratified by cause of death:

- ``deaths`` - count of simulants who died during each time step. The
  ``entity`` column contains the cause name or ``"other_causes"``.
- ``ylls`` - sum of remaining life expectancy at death (years of life lost).
  Uses the same cause-level breakdown as ``deaths``.

To produce non-zero values, the simulation needs a disease state with
non-zero ``excess_mortality_rate``.

.. testcode::

   healthy = SusceptibleState("test_cause")
   infected = DiseaseState("test_cause", excess_mortality_rate=build_cause_table(5.0))
   healthy.add_rate_transition(infected)
   fatal_model = DiseaseModel("test_cause", states=[healthy, infected])

   config = make_base_config()
   config.update({"population": {"population_size": 1000}}, layer="model_override")

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           fatal_model,
           MortalityObserver(),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   for _ in range(5):
       sim.step()

   results = sim.get_results()
   deaths = results["deaths"]
   print(deaths.columns.tolist())
   print(deaths["entity"].tolist())

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'value']
   ['test_cause', 'other_causes']

.. testcode::

   test_cause_deaths = deaths.loc[deaths["entity"] == "test_cause", "value"].iloc[0]
   assert test_cause_deaths > 0

   ylls = results["ylls"]
   test_cause_ylls = ylls.loc[ylls["entity"] == "test_cause", "value"].iloc[0]
   assert test_cause_ylls > 0


DisabilityObserver
------------------

A :class:`~vivarium.public_health.results.DisabilityObserver` registers one
observation:

- ``ylds`` - years lived with disability, computed as each simulant's
  disability weight multiplied by the time step duration, summed across
  simulants. Results are broken out by cause in the ``entity`` column,
  plus an ``"all_causes"`` total row.

It requires at least one disease state with a non-zero
``disability_weight``.

.. testcode::

   healthy = SusceptibleState("test_cause")
   infected = DiseaseState("test_cause", disability_weight=disease_disability_weight(0.3))
   healthy.add_rate_transition(infected)
   disability_model = DiseaseModel("test_cause", states=[healthy, infected])

   config = make_base_config()
   config.update({"population": {"population_size": 1000}}, layer="model_override")

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           disability_model,
           DisabilityObserver(),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   for _ in range(3):
       sim.step()

   results = sim.get_results()
   ylds = results["ylds"]
   print(ylds.columns.tolist())
   print(sorted(ylds["entity"].unique().tolist()))

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'stratification', 'value']
   ['all_causes', 'test_cause']

.. testcode::

   test_cause_ylds = ylds.loc[ylds["entity"] == "test_cause", "value"].iloc[0]
   assert test_cause_ylds > 0


CategoricalRiskObserver
-----------------------

A :class:`~vivarium.public_health.results.CategoricalRiskObserver` registers
one observation:

- ``person_time_{risk}`` - person-years spent in each exposure category,
  accumulated each time step. The ``sub_entity`` column contains the
  category name (e.g., ``"exposed"``, ``"unexposed"``). The
  ``entity_type`` is ``"rei"`` (risk/etiology/impairment).

.. testcode::

   config = make_base_config()
   config.update({"population": {"population_size": 1000}}, layer="model_override")

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           Risk("risk_factor.test_risk"),
           CategoricalRiskObserver("test_risk"),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   for _ in range(3):
       sim.step()

   results = sim.get_results()
   pt = results["person_time_test_risk"]
   print(pt.columns.tolist())
   print(sorted(pt["sub_entity"].tolist()))

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'value']
   ['exposed', 'unexposed']

.. testcode::

   assert all(pt["value"] > 0)
   exposed_pt = pt.loc[pt["sub_entity"] == "exposed", "value"].iloc[0]
   unexposed_pt = pt.loc[pt["sub_entity"] == "unexposed", "value"].iloc[0]
   assert exposed_pt > unexposed_pt


MicrodataObserver
-----------------

A :class:`~vivarium.public_health.results.MicrodataObserver` records the raw
values of a configured set of population columns for every simulant at each
time step. Unlike the other observers it does not stratify or aggregate - it
emits one row per simulant per step. Each row carries an ``event_time`` column
identifying the step it was recorded on, and the results from every step are
concatenated into a single table.

This makes it a general-purpose tool for capturing per-simulant microdata from
any simulation: you tell it which columns to record and it writes them out
verbatim, leaving any downstream aggregation to you.

List the columns to record under the observer's name. ``columns`` is required;
an empty list raises a configuration error.

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "microdata_observer": {"columns": ["age", "sex"]},
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           MicrodataObserver(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()
   sim.step()

   microdata = sim.get_results()["microdata_observer"]
   print(sorted(microdata.columns.tolist()))

.. testoutput::

   ['age', 'event_time', 'sex']

The table holds one row per simulant per step, so two steps of a 1000-simulant
population produce 2000 rows spanning two distinct ``event_time`` values:

.. testcode::

   print(len(microdata))
   print(microdata["event_time"].nunique())

.. testoutput::

   2000
   2


Recording only matching simulants
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Pass a list of Pandas query strings as ``filter`` to record only the simulants
that match. The conditions are AND-combined, so the example below keeps only
females aged 20 or older:

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "microdata_observer": {
               "columns": ["age", "sex"],
               "filter": ['sex == "Female"', "age >= 20"],
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[BasePopulation(), MicrodataObserver()],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()

   microdata = sim.get_results()["microdata_observer"]
   print(sorted(microdata["sex"].unique().tolist()))
   print((microdata["age"] >= 20).all())
   print(len(microdata) < 1000)

.. testoutput::

   ['Female']
   True
   True


Recording only certain time steps
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default every time step is recorded. Pass ``timesteps`` - a list of dates -
to record only the steps whose ``event_time`` matches one of them. This
simulation starts in 1990 with 30.5-day steps, so the first step's
``event_time`` is 1990-08-01 and the second is 1990-09-01; recording only the
latter leaves the first step empty:

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "microdata_observer": {"columns": ["age"], "timesteps": ["1990-09-01"]},
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[BasePopulation(), MicrodataObserver()],
       configuration=config,
       plugin_configuration=base_plugins,
   )

   sim.step()  # 1990-08-01 - not recorded
   print(sim.get_results()["microdata_observer"].empty)
   sim.step()  # 1990-09-01 - recorded
   recorded = sim.get_results()["microdata_observer"]
   print(recorded["event_time"].dt.strftime("%Y-%m-%d").unique().tolist())

.. testoutput::

   True
   ['1990-09-01']


Capping the number of recorded rows
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For large populations you may want only a sample. ``row_limit`` sets the
*total* number of rows across all observed steps; each observed step then
records a fresh random sample of ``row_limit // <number of observed steps>``
simulants. Here two observed steps and a limit of 200 record 100 simulants
each. If fewer than 100 simulants are eligible to be observed on the first 
time-step, we won't observe more on the second to reach our limit of 200 per 
simulation - each time-step is capped at 100.

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "microdata_observer": {
               "columns": ["age"],
               "timesteps": ["1990-08-01", "1990-09-01"],
               "row_limit": 200,
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[BasePopulation(), MicrodataObserver()],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()
   sim.step()

   microdata = sim.get_results()["microdata_observer"]
   print(microdata.groupby("event_time").size().tolist())

.. testoutput::

   [100, 100]


Following a closed cohort
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default ``row_limit`` draws a *fresh* sample each observed step, so the
recorded simulants differ from step to step. Set ``single_random_sample`` to
sample once from the initial population and then record only those same
simulants - a *closed cohort* - at every observed step. This requires
``row_limit``, which sets the cohort's size (``row_limit // <number of observed
steps>``).

The cohort is never refilled: members are dropped without replacement once they
leave the filter or the simulation, so the recorded count can only shrink over
time and ``row_limit`` stays an upper bound. Recording a stable per-simulant id
shows the same simulants recurring each step:

.. testcode::

   class SimulantID(Component):
       """Tag each simulant with a stable id so we can see which ones recur."""

       def setup(self, builder):
           builder.population.register_initializer(
               initializer=self._initialize, columns=["simulant_id"]
           )

       def _initialize(self, pop_data):
           self.population_view.initialize(
               pd.DataFrame(
                   {"simulant_id": range(len(pop_data.index))}, index=pop_data.index
               )
           )

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "microdata_observer": {
               "columns": ["simulant_id"],
               "timesteps": ["1990-08-01", "1990-09-01"],
               "row_limit": 200,
               "single_random_sample": True,
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[BasePopulation(), SimulantID(), MicrodataObserver()],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()
   sim.step()

   microdata = sim.get_results()["microdata_observer"]
   cohorts = microdata.groupby("event_time")["simulant_id"].apply(set)
   print(cohorts.map(len).tolist())
   print(cohorts.iloc[0] == cohorts.iloc[1])

.. testoutput::

   [100, 100]
   True


Stratification
--------------

A **stratification** splits observer output into sub-groups based on simulant
attributes (e.g. age group, sex, or custom categories). Each stratification
adds a column to the results table whose values identify which group each row
belongs to. You can include, exclude, or define custom stratifications per
observer.


Including a stratification
^^^^^^^^^^^^^^^^^^^^^^^^^^

Add a registered stratification to one observer via
``stratification.<observer_name>.include``. Here we include ``sex``, one of
the four stratifications registered by ``ResultsStratifier`` (``age_group``,
``current_year``, ``event_year``, and ``sex``):

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "stratification": {
               "test_cause": {
                   "include": ["sex"],
                   "exclude": [],
               },
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           SI("test_cause"),
           DiseaseObserver("test_cause"),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()

   pt = sim.get_results()["person_time_test_cause"]
   print(pt.columns.tolist())
   print(sorted(pt["sex"].unique().tolist()))
   print(len(pt))

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'sex', 'value']
   ['Female', 'Male']
   4


Excluding a default stratification
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Set global defaults with ``stratification.default``, then exclude specific
ones per observer with ``stratification.<observer_name>.exclude``:

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "stratification": {
               "default": ["age_group", "sex"],
               "test_cause": {
                   "include": [],
                   "exclude": ["age_group"],
               },
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           SI("test_cause"),
           DiseaseObserver("test_cause"),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()

   pt = sim.get_results()["person_time_test_cause"]
   # age_group excluded - only sex remains from defaults
   print(pt.columns.tolist())
   print(len(pt))

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'sex', 'value']
   4


Including a custom stratification
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Register a custom stratification from any component, then reference it by
name in the observer's ``include`` list:

.. testcode::

   import pandas as pd

   class AgeCohortStratifier(Component):
       """Register a binary young/old stratification."""

       def setup(self, builder: Builder) -> None:
           builder.results.register_stratification(
               "age_cohort",
               ["young", "old"],
               mapper=self.map_age_cohort,
               is_vectorized=True,
               requires_attributes=["age"],
           )

       @staticmethod
       def map_age_cohort(pop: pd.DataFrame) -> pd.Series:
           age = pop.squeeze(axis=1)
           return age.apply(lambda a: "young" if a < 50 else "old")

.. testcode::

   config = make_base_config()
   config.update(
       {
           "population": {"population_size": 1000},
           "stratification": {
               "test_cause": {
                   "include": ["age_cohort"],
                   "exclude": [],
               },
           },
       },
       layer="model_override",
   )

   sim = InteractiveContext(
       components=[
           BasePopulation(),
           SI("test_cause"),
           DiseaseObserver("test_cause"),
           AgeCohortStratifier(),
           ResultsStratifier(),
       ],
       configuration=config,
       plugin_configuration=base_plugins,
   )
   sim.step()

   pt = sim.get_results()["person_time_test_cause"]
   print(pt.columns.tolist())
   print(sorted(pt["age_cohort"].unique().tolist()))

.. testoutput::

   ['measure', 'entity_type', 'entity', 'sub_entity', 'age_cohort', 'value']
   ['old', 'young']

.. testcode::

   young_total = pt.loc[pt["age_cohort"] == "young", "value"].sum()
   old_total = pt.loc[pt["age_cohort"] == "old", "value"].sum()
   assert young_total > 0
   assert old_total > 0
