
The branches file
=================

When designing a model specification you are often interested in simulating many different scenarios of the model that
correspond to changes in the parameters you have defined. For example, let's assume you have defined a model
specification that includes a dietary intervention of egg supplementation and that this intervention is parameterized
by the proportion of the population that is recruited and the starting age of recruitment. We may want to run
simulations on several different proportions or try different starting ages, including useful cases like full
recruitment and no recruitment. We can do that easily with the following branches file

.. code-block:: yaml

    input_draw_count: 10
    random_seed_count: 5

    branches:
            - egg_intervention:
                    recruitment:
                        proportion: [0.0, 0.4, 0.8, 1.0]
                    recruitment:
                        age_start: [0.0, 1.0, 10.0, 20.0, 45.0]

The ``branches`` block specifies changes to values found in the model specification, exactly matching the blocks from
the specification.  Here, the list [0.0, 0.4, 0.8, 1.0] specifies values of recruitment proportion we wish to
simulate while the list [0.0, 1.0, 10.0, 20.0, 45.0] specifies starting recruitment ages. The product of these parameters is
used to define simulations, so this will result in 16 separate simulations of the model configuration, each with a
different combination of recruitment proportion and recruitment age start. This is a very convenient way to simulate
multiple scenarios with different values.

Additionally, there are two other useful top-level blocks: ``input_draw_count`` and ``random_seed_count``.
``input_draw_count`` specifies the number of input draws from the GBD to run the simulation on, drawn uniformly from
the total number of draws GBD produces, 1000. ``random_seed_count`` specifies the number of different random seeds to
run simulations with. Each of these is considered in the cartesian product of simulations as well.

To make this concrete, let's explicitly calculate how many simulations the above branches file will result in. This is
given by input_draw_count * random_seed_count * proportions * age_starts: 10 * 5 * 4 * 5 = 1000 simulations.

It is important to note that any configuration option that is natively specified as a list can **NOT**
be used in the branch file.  In other words, you cannot specify a list of lists. Also, you should remember that varying
the time step, start or end time, or the population size will make profiling jobs very difficult and runs the risk of
breaking our output writing tools. Keep this in mind when you write a branch file.
