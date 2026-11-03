Glossary
========

.. glossary::

  Model Specification
    A yaml file that details all components and configuration necessary to run
    a particular model.

  Branch Configuration
    A yaml file that lists the count of input data draws and random seeds as
    well as a set of simulation configuration options. When coupled with a
    model specification, this file defines a set of different simulation
    scenarios that can be run in parallel with the ``psimulate`` command
    line utility.

  Parameter Uncertainty
    Parameter uncertainty is uncertainty due to the input data. The Global
    Burden of Disease represents uncertainy distributions around the parameters
    it produces with :term:`draws<Input Draw>`. By running a simulation with
    several different draws of the input data, we can propagate the parameter
    uncertainty through our model and to our outputs.

  Stochastic Uncertainty
    Stochastic uncertainty is uncertainty due to the inherent variability in
    the model. This variability is represented in a variety of places by
    using random numbers to sample from distributions. Our simulations
    control stochastic uncertainty with :term:`random seeds<Random Seed>`.
    Stochastic uncertainty in simulations is deeply related to sampling
    uncertainty in a study. By holding the input parameters constant and
    varying the random seed, we can produce many realizations (or samples)
    of the same underlying population and use that to minimize the
    stochastic uncertainty.

  Input Draw
    A way of representing uncertainty in the input parameters. Rather than
    having an explicit distribution, each input draw of a parameter is
    a sample from the underlying distribution of that parameter in a population.
    Taken collectively, all the input draws form a numeric representation of
    the original parameter distribution.

  Random Seed
    A number used to seed the simulation's random number generator. Two
    simulations run with the same random seed will produce the same random
    numbers for each decision made in the simulation on each time step for
    each person.

  Configuration Parameter
    A parameter of a given model specified in the ``configuration`` block
    of the :term:`model specification<Model Specification>`. Typically
    determined early on in the model development process, these parameters
    determine what scenarios can be run with a given model.  Common
    configuration parameters include the target coverage of an intervention,
    what subset of a population the intervention targets, and how effective
    an intervention is.
