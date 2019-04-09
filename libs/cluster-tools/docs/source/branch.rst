=================
The Branches File
=================

.. contents::
    :depth: 2
    :local:
    :backlinks: none

When investigating a research question with the Vivarium framework, it usually becomes necessary to vary aspects of a
model configuration in order to evaluate the uncertainty of model outputs or to explore different scenarios based on
model parameters. Without any extra tooling this would require manually manipulating the configuration file and
re-running for each desired change which would quickly get out of hand. The branches file helps us do this in a
convenient way. This section will detail the common ways simulations are varied and the different aspects of a branches
file that help us do this.

Uncertainty
-----------

Generating uncertainty for results is a core tenant of IHME and this is no different for simulation science. We are
primarily concerned with uncertainty from two sources in our models -- uncertainty surrounding the data that goes in to
a model and uncertainty due to stochasticity. The branches file can help us explore these uncertainties easily by
allowing us to vary the input draws from the Global Burden of Disease (GBD) used as well as the random seeds used by the
simulation.

Data Uncertainty
^^^^^^^^^^^^^^^^
Our simulations primarily rely on GBD results which are produced in draws to estimate uncertainty. In order to reflect
the GBD's uncertainty surrounding their results in our simulations, we generally run simulations with a variety of
output draws.

.. note::
    A draw is a statistical term related to bootstrapping that has a specific meaning in the context of the GBD. The
    implementation details vary, but the purpose is for

    some quantity or measure of interest, a draw is a member of a set a full set of results such that, when taken
    together, the set of draws describes at least some of the uncertainty surrounding the quantity as a result of the
    modeling process, data uncertainty, etc. Generally, GBD results are produced in sets of 1000 draws.

To do this, we can use the ``input_draw_count`` key in a branches file. This key specifies an integer that represents
the number of different simulations that will be run in parallel, each with a different draw of GBD results drawn
uniformly from the number available, which is generally 1,000. Below is a very simple branches file containing only a
mapping that dictates the number of draws to run.

.. code-block:: yaml

    input_draw_count: 10

Stochastic Uncertainty
^^^^^^^^^^^^^^^^^^^^^^
Vivarium simulations are probabilistic in nature. They tend to contain distributions that describe quantities rather
than singular values. Because of this, our models are subject to stochastic uncertainty. To attempt to capture some of
this uncertainty we can run the simulation with different random seeds which will result in different numbers drawn from
the underlying pseudo-random number generator, and thus different output results. This can be specified in the branches
file with the ``random_seed_count`` key. This key specifies an integer that represents the number of different random
seeds to use, each generated randomly and run in a separate simulation.

.. note::
    Random seeds are a convenient way to scale up a simulation's population in parallel. For example, running a
    simulation with one million simulants and a single random seed is equivalent to running the same simulation with
    ten thousand people and 100 random seeds. However, because simulations specified with different seeds will be run
    in parallel (link psimulate), this is often preferable.

An example of specifying ``random_seed_count`` is below.

.. code-block:: yaml

    random_seed_count: 100

Combining Draws and Seeds
^^^^^^^^^^^^^^^^^^^^^^^^^
Since specifying either draws or seeds will result in multiple simulations being run, it is important to understand how
simulation configurations are determined when both are specified. The specification of multiple branch configurations
that would result in more than one simulation will lead to simulations for the cartesian product of the the
configuration sets. An example may make this clearer, so consider the following model specification.

.. code-block:: yaml

    input_draw_count: 100
    random_seed_count: 10

It combines the two configuration keys we just learned about. Taken separately, the ``input_draw_count`` mapping would
lead to 100 simulations on 100 random draws of input data while the ``random_seed_count`` mapping would lead to ten
simulations on ten randomly generated random seeds. With both specified, the result is one thousand total simulations,
one for each member of the cartesian product of those sets. That is, ten simulations with the ten random seeds for each
of the 100 GBD draws.

Parameter Variations
--------------------
A major function of branches files is to enable easy manipulation of the configuration parameters of a model
specification to generate different scenarios or examine the sensitivity of a model to changes in a specific parameter.
In the following sections we will describe a number of ways you can construct different scenarios and the effect on
the number of simulations these will have.

.. note::
    The following examples that alter configuration parameters all lie under a ``branches`` key.

Single Parameter Variation
^^^^^^^^^^^^^^^^^^^^^^^^^^
In order to illustrate the variation of a single parameter, let's assume you have defined a model specification file
that includes a dietary intervention of egg supplementation and that this intervention is parameterized by the
proportion of the population that is recruited into the intervention program. We may want to run simulations on several
different proportions including full recruitment and no recruitment, which would function as a baseline. We can easily
do this with the following branches file.

.. code-block:: yaml

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]

The ``branches`` block specifies changes to values found in the model specification YAML, exactly matching the blocks
from that specification underneath.  Here, the YAML list [0.0, 0.4, 0.8, 1.0] dictates specific recruitment proportions
to be simulated. Thus, you can expect four separate simulations to be run, one for each.

.. warning::
    Varying the time step, start or end time, or the population size of a simulation will make profiling very difficult
    and runs the risk of breaking our output writing tools.


Interaction with Uncertainty
^^^^^^^^^^^^^^^^^^^^^^^^^^^^
As touched upon in the section on :ref:`combining draws and seeds<Combining Draws and Seeds>`, when multiple branch
configurations would result in multiple simulations, the result is a simulation for every combination, or the cartesian
product, of the parameters. Let's add draws to our previous branches file and figure out how many simulations it will
result in.

.. code-block:: yaml

    input_draw_count: 100

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]

This branches file will result in 400 simulations, a set of 100 different draws for each recruitment proportion.

Dual Parameter Variation
^^^^^^^^^^^^^^^^^^^^^^^^
Branches files really shine when you want to vary a lot of aspects of your model. Let's add another parameter to create
scenarios along a new dimension. Say, for instance, we were also interested in the implementing the egg intervention
by recruiting people only once they pass a certain age threshold. Provided components were available that can implement
this,we could add a variety of starting ages to our branches file like so:

.. code-block:: yaml

    input_draw_count: 100

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]
                        age_start: [10.0, 25.0, 45.0, 65.0]

This will result in scenarios encompassing every combination of recruitment proportion and starting age. Specifically,
it will result in 100 separate simulations per combination, one for each randomly sampled draw. This means the total
number of simulations is given by draws * proportions * starting age, or 1,600 altogether.

.. note:: The top-level keys for controlling seeds and draws are the same across branches.

Complex Configurations
^^^^^^^^^^^^^^^^^^^^^^
Let's look at a final example with a bit more going on. This branches file alters a model that simulates a Shigellosis
vaccine intervention parameterized on dose administration ages and dose protection effects.

.. code-block:: yaml

    input_draw_count: 500
    random_seed_count: 4

    branches:
            - shigellosis_vaccine:
                    dose_age_range:
                            first:
                                    start: 270
                                    end: 300
                            second:
                                    start: 360
                                    end: 390
                            booster:
                                    start: 450
                                    end: 480
                            catchup:
                                    start: 450
                                    end: 480
                    protection:
                            dose_protection:
                                    first: 0.0
                                    second: 0.0
                                    booster: 0.0
                                    catchup: 0.0
            - shigellosis_vaccine:
                    dose_age_range :
                            first :
                                    start: 270
                                    end: 300
                            second :
                                    start: 360
                                    end: 390
                            booster :
                                    start: 450
                                    end: 480
                            catchup :
                                    start: 450
                                    end: 480
                    protection:
                            dose_protection:
                                    first: 0.7
                                    second: 1.0
                                    booster: 0.0
                                    catchup: 0.0

The :ref:`YAML List<Lists>` underneath the ``branches`` key denotes two different simulation scenario each with a set of
parameters. Because all of the children of those list entries are singular values, only one simulation is produced for
each. Since we have additionally specified counts for draws and random seeds, we will run a simulation for each of these
scenarios, for each combination of random seed and draw. This will result in 4,000 total simulations.
