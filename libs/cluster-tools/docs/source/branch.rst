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

Generating uncertainty for results is a core tenant of IHME and this is no different for simulation science. We are primarily concerned with two types
of uncertainty in our models -- uncertainty surrounding the parametrization of a model and uncertainty due to stochasticity. The branches file can help us
explore these uncertainties easily by allowing us to vary the GBD input draws used for a simulation as well as the random seeds used by numpy.

Parameter Uncertainty
^^^^^^^^^^^^^^^^^^^^^
Subsection on varying parameter uncertainty with an example branches file with just the input draw count

it is often useful to vary random seeds, draws of input data used from the Global Burden of Disease,

.. note::
    A draw is a statistical term related to bootstrapping that has a specific meaning in the context of the GBD: for some quantity or measure of interest, a
    draw is a member of a set a full set of results such that, when taken together, the set of draws describes at least some of the uncertainty surrounding the quantity as a
    result of the modeling process, data uncertainty, etc. Generally, GBD results are produced in sets of 1000 draws.

Stochastic Uncertainty
^^^^^^^^^^^^^^^^^^^^^^
Subsection on varying stochastic uncertainty with an example branches file with just the random seed count

    Note about how varying stochastic uncertainty allows aggregations, effectively letting us increase
    the sample size for a given input draw. (e.g. 1 sim with 1M people == 100 sims with 10k people).
    Subsection on varying both at the same time with description on how we arrive at the number of runs and example
    model spec w/ both input_draw_number and random_seed_count args.

running a simulation with 1M people is conceptually the same as running 100 sims with 10k people. However,  because simulations specified with different
seeds can be run in parallel this is often preferable.


Parameter Variations
--------------------

Intro: "the branches section defines a set of scenarios. We'll describe a number of ways you might want to construct
simple or complex scenario specifications..."

Single Parameters variation
^^^^^^^^^^^^^^^^^^^^^^^^^^^
Subsection with single parameter variation and example model spec with just the branches key.

Interaction with Uncertainty
^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Subsection about how this combines with the input_draw_number/random_seed_count.

Varying Two Scenarios
^^^^^^^^^^^^^^^^^^^^^
Subsection with variation of two parameters. Explanation of how this turns into multiple scenarios.

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
