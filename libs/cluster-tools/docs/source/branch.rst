=================
The Branches File
=================

.. contents::
    :depth: 2
    :local:
    :backlinks: none

When investigating a research question with the Vivarium framework, it usually becomes necessary to vary aspects of a model configuration in order to
evaluate the uncertainty of model outputs or to explore different scenarios based on model parameters. Without any extra tooling this would require
manually manipulating the configuration file and re-running for each desired change which would quickly get out of hand. The branches file helps us do
this in a convenient way. This section will detail the common ways simulations are varied and the different aspects of a branches file that help us do this.

Uncertainty
-----------

Generating uncertainty for results is a core tenant of IHME and this is no different for simulation science. We are primarily concerned with uncertainty
from two sources in our models -- uncertainty surrounding the data that goes in to a model and uncertainty due to stochasticity. The branches file can help
us explore these uncertainties easily by allowing us to vary the input draws from the Global Burden of Disease (GBD) used as well as the random seeds
used by the simulation.

Data Uncertainty
^^^^^^^^^^^^^^^^
Our simulations primarily rely on GBD results which are produced in draws to estimate uncertainty. In order to reflect the GBD's uncertainty surrounding their
results in our simulations, we generally run simulations with a variety of output draws.

.. note::
    A draw is a statistical term related to bootstrapping that has a specific meaning in the context of the GBD: for some quantity or measure of interest, a
    draw is a member of a set a full set of results such that, when taken together, the set of draws describes at least some of the uncertainty surrounding
    the quantity as a result of the modeling process, data uncertainty, etc. Generally, GBD results are produced in sets of 1000 draws.

To do this, we can use the ``input_draw_count`` key in a branches file. This key specifies an integer that represents the number of different simulations that
will be run in parallel, each with a different draw of GBD results drawn uniformly from the number available. Below is a very simple branches file, containing
only a mapping that dictates the number of draws to run.

.. code-block:: yaml
    input_draw_count: 10

Stochastic Uncertainty
^^^^^^^^^^^^^^^^^^^^^^
Vivarium simulations are probabilistic in nature. They tend to contain distributions that describe quantities rather than singular values. Because of this,
our models are subject to stochastic uncertainty. To attempt to capture this uncertainty we can run the simulation with a different random seed, which will
result in different numbers drawn from the underlying pseudo-random number generator, and a different candidate output space. This can be specified in the
branches file with the ``random_seed_count`` key. This key specifies an integer that represents the number of different random seeds to use, each generated
randomly and run in a separate simulation.

    Note about how varying stochastic uncertainty allows aggregations, effectively letting us increase
    the sample size for a given input draw. (e.g. 1 sim with 1M people == 100 sims with 10k people).
    Subsection on varying both at the same time with description on how we arrive at the number of runs and example
    model spec w/ both input_draw_number and random_seed_count args.

.. note::
    Random seeds are a convenient way to scale up a simulation's population in parallel. For example, running a simulation with one million simulants and a
    single random seed is equivalent to running the same simulation with ten thousand people and 100 random seeds. However, because simulations specified
    with different seeds will be run in parallel (link psimulate), this is often preferable.
    conceptually the same as

An example of specifying ``random_seed_count`` is below.

.. code-block:: yaml
    random_seed_count: 100

Combining Draws and Seeds
^^^^^^^^^^^^^^^^^^^^^^^^^
Since specifying either draws or seeds will result in multiple simulations being run, it is important to understand how simulation configurations are determined
when both are specified. The specification of multiple branch configuration that would result in more than one simulation will lead to simulations for the
cartesian product of the the configuration sets. For example, consider the following model specification.

.. code-block:: yaml
    input_draw_count: 100
    random_seed_count: 10

It combines the two configuration keys we just learned about. Taken separately, the ``input_draw_count`` mapping would lead to 100 simulations on 100 random draws
of input data, while the ``random_seed_count`` mapping would lead to ten simulations on ten randomly generated random seeds. With both specified, the result is one
thousand total simulations, one for each member of the cartesian product of those sets. That is, ten simulations with the ten random seeds for each of the 100 GBD
draws.

Parameter Variations
--------------------

Intro: "the branches section defines a set of scenarios. We'll describe a number of ways you might want to construct
simple or complex scenario specifications..."

Varying parameters can be useful both for exploring different scenarios and for evaluating the uncertainty around , or the sensitivity of the simulation to changes
in parameter values.

Single Parameters variation
^^^^^^^^^^^^^^^^^^^^^^^^^^^
Subsection with single parameter variation and example model spec with just the branches key.

Interaction with Uncertainty
^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Subsection about how this combines with the input_draw_number/random_seed_count.

Varying Two Scenarios
^^^^^^^^^^^^^^^^^^^^^
Subsection with variation of two parameters. Explanation of how this turns into multiple scenarios.

Complex Configurations
^^^^^^^^^^^^^^^^^^^^^^

########################################################################################################################

When trying to answer the research questions that drove the construction of a model and a configuration, it is often
useful to vary parameters of the configuration to simulate different scenarios.  Without any extra tooling, this would
require manually changing the configuration and re-running, which would quickly get out of hand. The branches file helps
us do this in a convenient way. For example, let's assume you have defined a model specification that includes a
dietary intervention of egg supplementation and that this intervention is parameterized by the proportion of the
population that is recruited and the starting age of recruitment. We may want to run simulations on several different
proportions like like full recruitment or no recruitment, or try a range of starting ages. We can do that easily with
the following branches file

.. code-block:: yaml

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]
                    recruitment:
                        age_start: [0.0, 1.0, 10.0, 20.0, 45.0]

The ``branches`` block specifies changes to values found in the model specification, exactly matching the blocks from
the specification (underneath the branches block).  Here, the YAML list [0.0, 0.4, 0.8, 1.0] specifies values of
recruitment proportions we wish to simulate while the list [0.0, 1.0, 10.0, 20.0, 45.0] specifies starting recruitment
ages. The cartesian product of these parameters is used to define simulations, so this will result in 20 separate
simulations of the model configuration, one for every combination of recruitment proportion and recruitment age start.
This is a very convenient way to simulate multiple scenarios with different values.

Additionally, there are two other useful top-level blocks: ``input_draw_count`` and ``random_seed_count``, shown in the
example below. Note that they lie outside the branches block. ``input_draw_count`` specifies the number of input draws
from the GBD to run the simulation on, drawn uniformly from the total number of draws GBD produces, 1000.
``random_seed_count`` specifies the number of different random seeds to run simulations with. Each of these is
considered in the cartesian product of simulations as well.

.. code-block:: yaml

    input_draw_count: 10
    random_seed_count: 5

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.2, 0.8]

To make this concrete, let's explicitly calculate how many simulations the above branches file will result in. This is
given by input_draw_count * random_seed_count * proportions: 10 * 5 * 2 = 100 simulations.

It is important to note that any configuration option that is natively specified as a list can **NOT**
be used in the branch file.  In other words, Vivarium does not accept a list of lists in a branches specification. Also,
you should remember that varying the time step, start or end time, or the population size will make profiling jobs very
difficult and runs the risk of breaking our output writing tools. Keep this in mind when you write a branch file.
