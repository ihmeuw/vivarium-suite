.. _exploration_tutorial:

================================================
Exploring a Simulation in an Interactive Setting
================================================

In other tutorials (:ref:`[Boids] <boids_tutorial>` and
:ref:`[Disease Model] <disease_model_tutorial>`) we've walked through how to build
components for simulations. We've also shown how to run those simulations
from the :ref:`command line <cli_tutorial>` and in an
:ref:`interactive setting <interactive_tutorial>`.

In this tutorial we'll focus on exploring simulations in an interactive
setting. The only prerequisite is that you've set up your programming
environment (See
:ref:`the getting started section <getting_started_tutorial>`). We'll look at
how to find your way around a simulation - what components, attributes, and
value pipelines it has, and what runs when - how to examine the
:term:`population state table <Population State Table>`, how to print and
interpret the simulation :term:`configuration <Configuration>`, and how to get
results out of it.

We'll work through all this with a few case studies using the simulations
built in the other tutorials.


.. contents::
   :depth: 2
   :local:
   :backlinks: none

What are we looking at?
-----------------------

Simulations are complicated things. It's beyond the scope of this tutorial
to talk about what they are and how they work and when they
make sense as models of the world. Luckily, once you have one in hand, you
can start figuring out the answers to many of those questions yourself.

In the case studies that follow, we'll start simply. We'll get our simulations
:ref:`setup <interactive_setup_tutorial>` in an interactive environment and
examine various aspects of the simulation state at the beginning
of the simulation. We'll then run them for a while and see how that state
changes over time. After we have a handle on examining different aspects
of the simulation, we'll take a step back to talk about what our expectations
should be about how the simulation should work and look at some examples
of how to test those expectations. Finally, we'll setup a comparison across
two simulations to examine how changing our
:term:`configuration parameters <Configuration>` alters what happens in a
simulation.

Getting things set up
---------------------

Before we can start exploring properties of the simulation, we need to get
our hands on an :class:`~vivarium.engine.interface.interactive.InteractiveContext`. This is
the object we'll use to examine and run our simulation model. You can check
out our tutorial on :ref:`setting up a simulation <interactive_setup_tutorial>`
to see the tools that ``vivarium-engine`` provides for building your own simulation
context objects. For this tutorial on exploring simulations, however,
we've provided a convenience function to get you started. In a Jupyter
notebook or python interpreter, you can run the following

.. testcode::

   from vivarium.engine.examples.disease_model import get_disease_model_simulation

   sim = get_disease_model_simulation()

The ``sim`` object returned here is our simulation context. With it, we're
ready to begin examining various aspects of the simulation state.

.. _exploration_introspection:

Finding your way around
-----------------------

Before looking at what a simulation *does*, it helps to be able to ask what it
*contains*. A context carries an index of itself, and the methods below are the
entry points. Almost everything here works on a freshly built simulation; the one
exception is called out below.

Components
++++++++++

:meth:`~vivarium.engine.interface.interactive.InteractiveContext.list_components`
returns a mapping of component name to component:

.. testcode::

   components = sim.list_components()
   assert isinstance(components, dict)
   print(len(components))
   # print the stringified mapping of the first two
   print({k: components[k].__repr__() for k in list(components)[0:2]})

.. testoutput::

   15
   {'base_population': 'BasePopulation()', 'mortality': 'Mortality()'}

In addition to the mapping returned by ``list_components``, if you know the name
of a component, you can get it via
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_component`.
``get_component`` is the exception mentioned above: it is restricted to certain
lifecycle states, and a context that has been set up but not yet stepped is not
one of them, so it has to be called after the simulation has started moving.

.. testcode::

   sim.step()
   print(sim.get_component("mortality").__repr__())

.. testoutput::

   Mortality()

What runs, and when
+++++++++++++++++++

Two methods print the order in which things happen.
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.print_lifecycle_order`
shows the simulation's lifecycles and the components that run in each state in
the order that they fire. See :ref:`the simulation lifecycle concept docs <lifecycle_concept>`
for what the phases and states are.

.. note::

    Components at the same priority level have no guaranteed order relative to
    one another!

.. testcode::

    sim.print_lifecycle_order()

.. testoutput::
    :options: +NORMALIZE_WHITESPACE

    initialization
        initialization
    setup
        setup
        post_setup
            ValuesManager(values_manager).on_post_setup
            PopulationManager(population_manager).on_post_setup
            DateTimeClock(datetime_clock).on_post_setup
            EventManager(event_manager).on_post_setup
            LookupTableManager(lookup_table_manager).on_post_setup
            DiseaseModel(disease_model.lower_respiratory_infections).on_post_setup
        population_creation
    main_loop*
        time_step__prepare
            DeathsObserver(deaths_observer).on_time_step_prepare
        time_step
            BasePopulation(base_population).on_time_step
            Mortality(mortality).on_time_step
            DiseaseModel(disease_model.lower_respiratory_infections).on_time_step
        time_step__cleanup
            DiseaseModel(disease_model.lower_respiratory_infections).on_time_step_cleanup
        collect_metrics
    simulation_end
        simulation_end
        report

:meth:`~vivarium.engine.interface.interactive.InteractiveContext.print_initializer_order`
shows the order in which components populate new simulants.

.. note::

    Initializers run in *dependency* order and no order is guaranteed between
    initializers that do not depend on each other!

.. testcode::
   
    sim.print_initializer_order()

.. testoutput::

    DateTimeClock(datetime_clock).initialize_individual_clock
    Mortality(mortality).initialize_is_alive
    BasePopulation(base_population).initialize_entrance_time_and_age
    DeathsObserver(deaths_observer).initialize_previous_alive
    BasePopulation(base_population).initialize_sex
    DiseaseModel(disease_model.lower_respiratory_infections).initialize_state
    Risk(risk.child_wasting).initialize_propensity

The event system is reachable too (Refer to :ref:`the event concept docs <event_concept>`).
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.list_events`
names the event channels, and
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_listeners`
returns the listeners on one channel, keyed by priority. For example, to list
all events and then determine any components with registered "time_step" 
listeners:

.. testcode::

    print(sim.list_events())
    print(sim.get_listeners("time_step").keys())
    for listener in sim.get_listeners("time_step")[5]:
        print(listener.__repr__())

.. testoutput::

    ['post_setup', 'time_step', 'time_step__cleanup', 'time_step__prepare', 'collect_metrics', 'simulation_end', 'report']
    dict_keys([5])
    <bound method BasePopulation.on_time_step of BasePopulation()>
    <bound method Mortality.on_time_step of Mortality()>
    <bound method Machine.on_time_step of DiseaseModel(state_column=lower_respiratory_infections)>

We see in the example above that ``BasePopulation``, ``Mortality``, and
the lower respiratory infections ``DiseaseModel`` each registered a listener
to the ``time_step`` phase, all at priority 5.

.. note::

    :meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_emitter`
    is also present on the context, but it is restricted to the ``setup``,
    ``simulation_end`` and ``report`` lifecycle states, so it is not callable
    from a session that is simply set up or mid-run. Emitting events by hand is
    discouraged in any case; see the :ref:`event system concept page <event_concept>`
    for why, and prefer registering listeners.

State table attributes and value pipelines
++++++++++++++++++++++++++++++++++++++++++

This is the distinction that causes the most confusion, so it is worth being
precise. A simulation exposes two things that are similar but not identical:
*attributes / attribute pipelines* and *value pipelines*.

:term:`Attributes <Attribute>` - computed by
:term:`attribute pipelines <Attribute Pipeline>` - are simulant-specific values;
they are the columns of the :term:`state table <Population State Table>`.
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_attribute_names`
lists the names of all of the attributes in a given simulation and
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_population`
gets their current values.

.. code-block:: python

    attribute_names = sim.get_attribute_names()
    print(len(attribute_names))
    # list four of them
    print(attribute_names[0:4])
    print(sim.get_population(["age", "sex"]).head())

::

    24
    ['entrance_time', 'age', 'sex', 'mortality_rate']
            age     sex
    0  1.709032  Female
    1  2.733035    Male
    2  0.512616    Male
    3  2.900084  Female
    4  1.383266  Female

.. testcode::
    :hide:

    fresh = get_disease_model_simulation()
    attribute_names = fresh.get_attribute_names()
    assert len(attribute_names) == 24
    assert attribute_names[0:4] == ["entrance_time", "age", "sex", "mortality_rate"]
    pop = fresh.get_population(["age", "sex"])
    assert list(pop.columns) == ["age", "sex"]
    assert set(pop["sex"]) == {"Female", "Male"}
    assert pop["age"].between(0, 5).all()

An attribute pipeline (which produces attributes) is a specific type of the more
generic :term:`Pipeline`, which produces :term:`values <Value>`. While attributes
are simulant-specific and tabular in shape, values can be anything at all.
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.list_values`
lists the names of any available values and
:meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_value`
returns the corresponding pipeline object itself.

.. testcode::

   print(sim.list_values())
   print(sim.get_value("simulant_step_size").__repr__())

.. testoutput::

   ['simulant_step_size']
   _Pipeline(simulant_step_size)

Note how short that list is! Most of the quantities you might think of as being 
generated by pipelines - mortality rate, the disease transition rates, etc - 
are *attributes*, not *values*, and so they will not appear here. Note that
calling ``get_value()`` on an attribute will raise rather than returning anything
and its error message will point you back at attributes.

.. testcode::

   try:
       sim.get_value("mortality_rate")
   except ValueError as e:
       print(e)

.. testoutput::

   No value pipeline 'mortality_rate' registered. Are you looking for an attribute pipeline?

.. note::

    There is no public method to access an AttributePipeline object; you can read
    the attribute's values with ``get_population()``, but not inspect how it is
    computed.

.. _interactive_results:

Observed results
----------------

While the state table is always available for inspection in an interactive
context (via :meth:`~vivarium.engine.interface.interactive.InteractiveContext.get_population`),
results are not observed by default. Any
:class:`observers <vivarium.engine.framework.results.observer.Observer>` the
model declares are still registered and still maintain their own state-table
columns; only the per-step results gathering is switched off. Refer to the
:ref:`results system concept page <results_concept>` for more details about
observers and observations.

.. testcode::

    from vivarium.engine import InteractiveContext
    from vivarium.engine.examples.disease_model import get_model_specification_path

    sim = InteractiveContext(get_model_specification_path())
    sim.take_steps(10)
    print(sim.get_results())

.. testoutput::

    {}

Pass ``observe=True`` to collect them:

.. code-block:: python

    sim = InteractiveContext(get_model_specification_path(), observe=True)
    sim.take_steps(10)
    for metric, results in sim.get_results().items():
        print(metric)
        print(results)

::

    dead
      stratification  value
    0            all   23.0
    ylls
      stratification        value
    0            all  1989.372585

.. testcode::
    :hide:

    from vivarium.engine import InteractiveContext
    from vivarium.engine.examples.disease_model import get_model_specification_path
    from vivarium.fuzzy_checker import FuzzyChecker

    fuzzy_checker = FuzzyChecker()

    sim = InteractiveContext(get_model_specification_path(), observe=True)
    sim.take_steps(10)

    results = sim.get_results()
    population_size = sim.configuration.population.population_size
    deaths = int(results["dead"]["value"].iloc[0])
    ylls = results["ylls"]["value"].iloc[0]

    # FIXME [MIC-7458]
    # Guard the numbers shown above against going empty or absurd. Deliberately
    # not a calibration check on the model's mortality; the example's configured
    # rates do not reach the pipelines they appear to configure, so the disease
    # has no mortality effect and a band derived from the config would encode a
    # wrong model of the example rather than check it.
    assert 0 < deaths < population_size / 100
    fuzzy_checker.assert_proportion(
        deaths,
        population_size,
        (1e-4, 1e-3),
        name="exploration_tutorial_deaths",
    )

    # YLLs are a sum of years, not a proportion, so the fuzzy checker does not
    # apply. Every death contributes life_expectancy minus the simulant's age,
    # and simulants are aged 0 to age_end, which bounds the per-death value.
    life_expectancy = sim.configuration.mortality.life_expectancy
    age_end = sim.configuration.population.age_end
    assert life_expectancy - age_end < ylls / deaths <= life_expectancy

We see above that there are two results being observed in this simulation:
the total number of dead simulants and the total years of lives lost (ylls).
Indeed, we see that there are two Observers registered in this simulation:

.. testcode::

    print([component for component in sim.list_components() if "observer" in component])

.. testoutput::

    ['deaths_observer', 'ylls_observer']

Defaulting to *not* observing results is a deliberate difference from a
command-line run, which *always* observes. Typically, interactive contexts
are used for inspecting the state table and pipelines and so there is no need
to spend time observing results.

Note that because the observers' listeners are registered during setup,
``observe`` has to be decided when the InteractiveContext is constructed; it
cannot be changed during a simulation.

Case study #1: population epidemiology
--------------------------------------

In this case study, we're going to put together and examine an individual-based
epidemiology model from a bunch of pre-constructed parts. We'll start out
rather mechanically, just showing how to set up and run a simulation and pull
out interesting data. As we go on, we'll talk about what sort of results
we should expect from the structure of the model and how we can verify those
expectations.

Checking out the configuration
++++++++++++++++++++++++++++++

One of the things we might want to look at is the simulation
:term:`configuration <Configuration>`. Typically, a
:term:`model specification <Model Specification>` encodes some configuration
information, but leaves many things set to defaults. We can see what's in the
configuration by simply printing it.

.. testsetup:: configuration

    from vivarium.engine.examples.disease_model import get_disease_model_simulation

    sim = get_disease_model_simulation()

    del sim.configuration['input_data']
    del sim.configuration['stratification']['excluded_categories']

.. testcode:: configuration

   print(sim.configuration)

.. testoutput:: configuration

    randomness:
        key_columns:
            model_override: ['entrance_time', 'age']
        map_size:
            component_configs: 1000000
        random_seed:
            component_configs: 0
        additional_seed:
            component_configs: None
        rate_conversion_type:
            component_configs: linear
    time:
        start:
            year:
                model_override: 2022
            month:
                model_override: 1
            day:
                model_override: 1
        end:
            year:
                model_override: 2026
            month:
                model_override: 12
            day:
                model_override: 31
        step_size:
            model_override: 0.5
        standard_step_size:
            component_configs: None
    population:
        population_size:
            model_override: 100000
        age_start:
            model_override: 0
        age_end:
            model_override: 5
    mortality:
        mortality_rate:
            model_override: 0.0114
        life_expectancy:
            model_override: 88.9
        data_sources:
            mortality_rate:
                component_configs: 0.01
    lower_respiratory_infections:
        incidence_rate:
            model_override: 0.871
        remission_rate:
            model_override: 45.1
        excess_mortality_rate:
            model_override: 0.634
    child_wasting:
        proportion_exposed:
            model_override: 0.0914
    effect_of_child_wasting_on_infected_with_lower_respiratory_infections.incidence_rate:
        relative_risk:
            model_override: 4.63
    sqlns:
        effect_size:
            model_override: 0.18
    interpolation:
        order:
            component_configs: 0
        validate:
            component_configs: True
        extrapolate:
            component_configs: True
    stratification:
        default:
            component_configs: []
        deaths:
            exclude:
                component_configs: []
            include:
                component_configs: []
        ylls:
            exclude:
                component_configs: []
            include:
                component_configs: []
    disease_state.susceptible_to_lower_respiratory_infections:
        data_sources:
            initialization_weights:
                component_configs: 1.0
            excess_mortality_rate:
                component_configs: 0.0
    disease_state.infected_with_lower_respiratory_infections:
        data_sources:
            initialization_weights:
                component_configs: 0.0
            excess_mortality_rate:
                component_configs: 0.0


What do we see here? The configuration is *hierarchical*. There are a set of
top level *keys* that define named subsets of configuration data. We can access
just those subsets if we like.

.. testcode::

    print(sim.configuration.randomness)

.. testoutput::

    key_columns:
        model_override: ['entrance_time', 'age']
    map_size:
        component_configs: 1000000
    random_seed:
        component_configs: 0
    additional_seed:
        component_configs: None
    rate_conversion_type:
        component_configs: linear

This randomness subset of configuration data contains more keys. All of the keys
in our example here (key_columns, map_size, random_seed, additional_seed,
and rate_conversion_type) point directly to values. We can access these values from the simulation
as well.

.. testcode::

    print(sim.configuration.randomness.key_columns)
    print(sim.configuration.randomness.map_size)
    print(sim.configuration.randomness.random_seed)
    print(sim.configuration.randomness.additional_seed)
    print(sim.configuration.randomness.rate_conversion_type)


.. testoutput::

    ['entrance_time', 'age']
    1000000
    0
    None
    linear

.. note::

    It appears that there should be one more layer of keys (``component_configs``
    or ``model_override``). This is a byproduct of the fact that the simulation
    configuration is a :class:`~vivarium.config_tree.main.ConfigTree`; those
    "hidden" keys reflect a priority level in the way the simulation configuration
    is managed. ``component_configs`` tells us that the corresponding values
    were set by a simulation component's ``configuration_defaults``, while
    ``model_override`` tells us that a model specification file set the value.

If you're trying to debug issues, you may want more information than this. You
can also type ``repr(sim.configuration)`` (this is the equivalent of evaluating
``sim.configuration`` in a jupyter notebook or ipython cell). This will
give you considerable information about where each configuration value was
set and at what priority level. You can read more about how the
configuration works in the
:ref:`configuration concept section <configuration_concept>`

.. _frozen_configuration:

One final note: because the simulation has already been setup, you can no longer
modify the configuration. You must modify the configuration prior to 
constructing the ``InteractiveContext``, or by constructing it via
`sim = InteractiveContext(..., setup=False)` and then calling `sim.setup()`
when ready to proceed.

.. testcode::

    from vivarium.config_tree import ConfigurationError

    try:
        sim.configuration.randomness.random_seed = 5
    except ConfigurationError:
        print("Can't update configuration after setup")

.. testoutput::

    Can't update configuration after setup

Looking at the simulation population
++++++++++++++++++++++++++++++++++++

Another interesting thing to look at at the beginning of the simulation is
your starting population.

.. code-block:: python

    pop = sim.get_population(
        [
            "age",
            "is_alive",
            "entrance_time",
            "lower_respiratory_infections",
            "child_wasting_propensity",
        ]
    )
    print(pop.head())

::

            age  is_alive       entrance_time                 lower_respiratory_infections  child_wasting_propensity
    0  1.721361      True 2021-12-31 12:00:00  susceptible_to_lower_respiratory_infections                  0.979461
    1  2.745364      True 2021-12-31 12:00:00  susceptible_to_lower_respiratory_infections                  0.170346
    2  0.524944      True 2021-12-31 12:00:00  susceptible_to_lower_respiratory_infections                  0.593326
    3  2.912412      True 2021-12-31 12:00:00  susceptible_to_lower_respiratory_infections                  0.725294
    4  1.395594      True 2021-12-31 12:00:00  susceptible_to_lower_respiratory_infections                  0.724847

This gives you a ``pandas.DataFrame`` representing your starting population.
You can use it to check all sorts of characteristics about individuals or
the population as a whole.

For example, summary statistics give you a quick look at the population's
demographics (your exact values will vary with the random draws):

.. code-block:: python

    pop = sim.get_population(["age", "sex"])
    print(pop.age.describe())
    print(pop.sex.value_counts())

::

    count    100000.000000
    mean          2.504026
    std           1.441763
    min           0.013709
    25%           1.255798
    50%           2.501152
    75%           3.744254
    max           5.013655
    Name: age, dtype: float64
    sex
    Female    50135
    Male      49865
    Name: count, dtype: int64

.. testcode::
    :hide:

    fresh = get_disease_model_simulation()

    pop = fresh.get_population(
        [
            "age",
            "sex",
            "is_alive",
            "entrance_time",
            "lower_respiratory_infections",
            "child_wasting_propensity",
        ]
    )
    assert len(pop) == 100_000
    assert pop["age"].between(0, 5).all()
    assert pop["is_alive"].all()
    assert (
        pop["lower_respiratory_infections"]
        == "susceptible_to_lower_respiratory_infections"
    ).all()
    assert pop["entrance_time"].nunique() == 1
    assert set(pop["sex"]) == {"Female", "Male"}
    from vivarium.fuzzy_checker import FuzzyChecker

    FuzzyChecker().assert_proportion(
        int(pop["sex"].eq("Female").sum()), len(pop), 0.5, name="tutorial_sex_split"
    )
    # Propensities are uniform on [0, 1].
    propensity = pop["child_wasting_propensity"]
    assert propensity.between(0, 1).all()
    assert abs(propensity.mean() - 0.5) < 5 * (1 / 12 / len(pop)) ** 0.5
    assert abs(propensity.std() - (1 / 12) ** 0.5) < 0.005


Understanding the simulation data
+++++++++++++++++++++++++++++++++

Our model starts with a bunch of people with uniformly distributed ages and
sexes. They march through time half a day at a time, set by ``step_size`` in
the ``time`` block of the configuration shown above. On each step for each
person, the simulation will ask and answer several questions: Did they die?
Did they get sick? If they were sick, did they recover? Are they exposed to
any risks? At the end we'll examine how many people died and compare that with
a theoretical life expectancy. Later, we'll consider two simulations that differ
only by the presence of a new intervention and examine how effective that
intervention is.

.. todo::
   Show how to understand the starting population from both the configuration
   and the population state table. Show how to understand the simulation time
   and how the clock progresses based on configuration parameters.


Case Study #2: Boids
--------------------

.. todo::
   Everything
