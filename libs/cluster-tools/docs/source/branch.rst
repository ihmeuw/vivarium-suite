=================
The Branches File
=================

.. contents::
    :depth: 2
    :local:
    :backlinks: none

When investigating a research question with the Vivarium framework, it usually becomes necessary to vary aspects of a
:term:`model specification<Model Specification>` in order to evaluate the uncertainty of model outputs or to explore
different scenarios based on model parameters. Without any extra tooling this would require manually manipulating
the model specification file and re-running for each desired change, which would quickly get out of hand.
The :term:`branch configuration<Branch Configuration>` helps us do this in a convenient way. This section will detail
the common ways simulations are varied and the different aspects of a branch configuration that help us do this.

Uncertainty
-----------

Generating uncertainty for results is a core tenant of IHME and this is no different for simulation science. We are
primarily concerned with two kinds of uncertainy in our model -- :term:`parameter uncertainy<Parameter Uncertainty>`
and :term:`stochastic uncertainty<Stochastic Uncertainty>`. The branch configuration can help us explore both sources
of uncertainty by varying both the :term:`input draw<Input Draw>` of the parameter data and the
:term:`seed<Random Seed>` of the simulation's random number generator.

Parameter Uncertainty
^^^^^^^^^^^^^^^^^^^^^
Our simulations primarily rely on results from the Global Burden of Disease (GBD). GBD results are produced with
:term:`uncertainy<Parameter Uncertainty>` represented as :term:`draws<Input Draw>`. Once we have a model we trust,
we typically want to capture our uncertainty in the input data by running the simulation model for several different
input draws.

.. note::

    A draw is a statistical term related to bootstrapping that has a specific meaning in the context of the GBD. The
    implementation details vary, but the purpose is for some quantity or measure of interest, a draw is a member of
    a full set of results such that, when taken together, the set of draws describes at least some of the uncertainty
    surrounding the quantity as a result of the modeling process, data uncertainty, etc. Generally, GBD results are
    produced in sets of 1000 draws.

To do this, we can use the ``input_draw_count`` key in a :term:`branch configuration<Branch Configuration>`.
This key refers to an integer that represents the number of different input draws to generate simulations from.

.. code-block:: yaml
    :caption: parameter_uncertainty_branches.yaml

    input_draw_count: 10

When we use this branch configuration along with the original :term:`model specification<Model Specification>`,
we'll launch 10 simulations in parallel, each using a different set of input parameters represented by the
draw number.

.. code-block:: sh

  psimulate run /path/to/model_specification.yaml /path/to/parameter_uncertainty_branches.yaml


.. note::

  ``psimulate`` randomly selects the input draws it uses from the range [0, 999].  The selection
  happens without replacement, so specifying an ``input_draw_count`` of 10 guarantees you
  10 unique input draws.


Stochastic Uncertainty
^^^^^^^^^^^^^^^^^^^^^^
Vivarium simulations are probabilistic in nature. They use Monte Carlo sampling techniques to make decisions about
who gets sick, who goes to the hospital, who dies, etc. This usage of randomness means our models have to
consider the impact of :term:`stochastic uncertainty<Stochastic Uncertainty>` on its outputs.

There are two ways to handle stochastic uncertainty. The first is to increase the size of the population you're
simulating. This will wash out outlier cases that might heavily skew your results. This works fine up to a point,
but simulation run time scales directly with the size of the population you're simulating.  Alternatively,
you can run multiple simulations with different :term:`random seeds<Random Seed>` and aggregate your results across
those simulations. This second approach takes advantage of parallel computing to keep run times under control.

.. note::
   
    Random seeds are a convenient way to scale up a simulation's population in parallel. For example, running a
    simulation with one million simulants and a single random seed is equivalent to running the same simulation with
    ten thousand people and 100 random seeds. Because simulations specified with different seeds will be run
    in parallel, the latter run strategy is often preferable.   

To run our simulation for multiple random seeds, we use the ``random_seed_count`` key in a
:term:`branch configuration<Branch Configuration>`. This key specifies an integer that represents the number of
different random seeds to use, each generated randomly and run in a separate simulation.

.. code-block:: yaml
    :caption: stochastic_uncertainty_branches.yaml

    random_seed_count: 100

When we use this branch configuration along with the original :term:`model specification<Model Specification>`,
we'll launch 100 simulations in parallel, each using a different random seed.

.. code-block:: sh

  psimulate run /path/to/model_specification.yaml /path/to/stochastic_uncertainty_branches.yaml


Combining Draws and Seeds
^^^^^^^^^^^^^^^^^^^^^^^^^
Since specifying either :term:`input draws<Input Draw>` or :term:`random seeds<Random Seed>` will result in multiple
simulations being run, it is important to understand how :term:`branch configurations<Branch Configuration>` are
parsed into simulations when both keys are specified. Specifying both an ``input_draw_count`` and a
``random_seed_count`` will result in a set of input draws and a set of random seeds being independently
generated. Simulations will then be run for each unique combination of input draw and random seed (the
Cartesian product of the two sets).

An example may make this clearer, so consider the following model specification.

.. code-block:: yaml
    :caption: combined_uncertainty_branches.yaml

    input_draw_count: 100
    random_seed_count: 10

It combines the two configuration keys we just learned about. Taken separately, the ``input_draw_count`` mapping would
lead to 100 simulations on 100 draws of input data while the ``random_seed_count`` mapping would lead to ten
simulations on with identical input data but a different seed for the random number generation. With both specified,
the result is 1,000 total simulations, one for each member of the Cartesian product of those sets. That is,
we would run ten simulations with the ten random seeds for each of the 100 input data draws.

Configuration Variations
------------------------

A major function of :term:`branch configurations<Branch Configuration>` is to enable easy manipulation of
the :term:`configuration parameters<Configuration Parameter>` of a :term:`model specification<Model Specification>`.
These parameters generally govern interesting features of an intervention, such as its target coverage or efficacy.

Within a branch configuration, you can specify several variations of these parameters to generate different
scenarios or examine the sensitivity of a model to changes in a specific parameter. In the following sections we
will describe a number of ways you can construct different scenarios and explain how to compute the number of
simulations that will be run for a particular branch configuration.

.. note::

    The following examples that alter configuration parameters all lie under a ``branches`` key. This is the only
    other top level key (besides ``input_draw_count`` and ``random_seed_count``) that ``psimulate`` understands
    how to parse.

Single Parameter Variation
^^^^^^^^^^^^^^^^^^^^^^^^^^

In order to illustrate the variation of a single :term:`parameter<Configuration Parameter>`, let's assume
you have defined a :term:`model specification<Model Specification>` that includes a dietary intervention of
egg supplementation and that this intervention is parameterized by the proportion of the population that is recruited
into the intervention program. We may want to run simulations on several different proportions including full
recruitment and no recruitment, which would function as a baseline. We can easily do this with the following
branches file.

.. code-block:: yaml
    :caption: egg_intervention_branches.yaml

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]

The ``branches`` block specifies changes to values found in the configuration block of the original model specification
YAML. The block found in the branches file must exactly match the block from the original model specification.
Here, the YAML list [0.0, 0.4, 0.8, 1.0] dictates specific recruitment proportions to be simulated.
Thus, you can expect four separate simulations to be run, one for each variation.

.. warning::

    Varying the time step, start or end time, or the population size of a simulation will make profiling very difficult
    and runs the risk of breaking our output writing tools.


Interaction with Uncertainty
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As touched upon in the section on :ref:`combining draws and seeds<Combining Draws and Seeds>`, each of the top
level keys in a :term:`branch configuration<Branch Configuration>` can be independently produce a set of simulations
to be run.  To find the total set of simulations to be run from a branch configuration file, we need to count
the Cartesian product of the top level keys.  We'll use a slight alteration of our intervention configuration
as an example.


.. code-block:: yaml
    :caption: egg_intervention_with_parameter_uncertainty_branches.yaml

    input_draw_count: 100
    random_seed_count: 4

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]

This branch configuration will produce 400 simulations. First we consider the space of 
:term:`configuration parameters<Configuration Parameter>` the simulation will be run for: one scenario for 
each of the four recruitment proportions.  For each scenario, we will run a simulation for each combination
of :term:`input draw<Input Draw>` and :term:`random seed<Random Seed>` specified byt the ``input_draw_count`` 
and ``random_seed_count`` keys.  So we'll have:
``(Number of input draws) * (Number of random seeds) * (Number of scenarios) = 100 * 4 * 4 = 1600`` 
simulations to run from this branch configuration.

Multi-parameter Variation
^^^^^^^^^^^^^^^^^^^^^^^^^

:term:`Branch configurations<Branch Configuration>` really shine when you want to vary a lot of aspects of your model.

Let's add another :term:`parameter<Configuration Parameter>` to create scenarios along a new dimension. Say, for instance,
we were also interested in the implementing the egg intervention by recruiting people only once they pass a certain age
threshold. Provided components were available that can implement this, we could add a variety of starting ages to our
branches file like so:

.. code-block:: yaml
    :caption: egg_intervention_with_ages_branches.yaml

    input_draw_count: 100

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]
                        age_start: [10.0, 25.0, 45.0, 65.0]

This will result in scenarios encompassing every combination of recruitment proportion and starting age. Additionally,
it will result in 100 simulations for each one of the scenarios, one for each of the :term:`input draws<Input Draw>`.
This means the total number of simulations is given by ``(Number of input draws) * (Number of recruitment proportions)
* (Number of starting ages)`` giving a total of 1600 simulations.

Complex Configurations
^^^^^^^^^^^^^^^^^^^^^^

Let's look at a final example with a bit more going on. Note that in our last example
:term:`branch configuration<Branch Configuration>` we did significantly more work than we needed to. When our recruitment
proportion is ``0``, it doesn't matter what age we start recruiting people at.  This caused us to run 300 more simulations
than we needed to.  How do we write a better branch configuration?

.. code-block:: yaml
    :caption: better_egg_intervention_with_ages_branches.yaml

    input_draw_count: 100
    random_seed_count: 4

    branches:
            # Baseline scenario
            - egg_intervention:
                  recruitment:
                      proportion: 0.0
            # Intervention variations
            - egg_intervention:
                  recruitment:
                      proportion: [0.4, 0.8, 1.0]
                      age_start: [10.0, 25.0, 45.0, 65.0]

The :ref:`YAML List<Lists>` underneath the ``branches`` key denotes two different simulation scenario branches
each with a set of :term:`configuration parameters<Configuration Parameter>`. We resolve each one of the list
items under the ``branches`` key separately.  The first block resolves to a single baseline scenario.
The second block resolves to three different recruitment proportions for four different ages, which produces
a total of 12 intervention scenarios.  Thus the entire ``branches`` block resolves to 13 different sets of
configuration parameters.

Following the same logic as in the previous section, we compute the total number of simulations to be run as
``(Number of input draws) * (Number of random seeds) * (Number of scenarios) = 100 * 4 * 13 = 5200``.
