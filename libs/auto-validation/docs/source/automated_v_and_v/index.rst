..
  Section title decorators for this document:
  
  ==============
  Document Title
  ==============
  Section Level 1
  ---------------
  Section Level 2
  +++++++++++++++
  Section Level 3
  ~~~~~~~~~~~~~~~
  Section Level 4
  ^^^^^^^^^^^^^^^
  Section Level 5
  '''''''''''''''

  The depth of each section level is determined by the order in which each
  decorator is encountered below. If you need an even deeper section level, just
  choose a new decorator symbol from the list here:
  https://docutils.sourceforge.io/docs/ref/rst/restructuredtext.html#sections
  And then add it to the list of decorators above.

.. _automated_v_and_v:

.. role:: underline
    :class: underline

=========================================================
Automated verification and validation (V&V)
=========================================================

.. contents::
   :local:

Background/Motivation
---------------------

Our team uses :ref:`a process called verification and validation (V&V) <vivarium_best_practices_results_processing>`
to ensure that our models are behaving as we expect.
:ref:`The current process for V&V <vivarium_v_and_v_process>`
works well but can be quite labor intensive.
Due to the long runtime of simulations and the number of people involved,
each V&V cycle generally takes *at least* one day, which makes it difficult to find and fix issues quickly.

Automating more of the V&V process would help us catch issues faster and with less work.
We can **incrementally** automate V&V by building tools to do the repetitive parts,
and over time stitching these tools together to make the process more and more automatic.

As we develop the tools, there are also some process questions to work out about this transformation:

* Who investigates the cause of automated V&V failures?
  If the answer is "it depends on the failure," how do we route the right failures to the right people?
* How and when do we run automated V&V?

Automated V&V comparisons
-------------------------

Automated V&V plots
-------------------

Automated V&V checks
--------------------

Automating V&V checks refers to replacing the manual process of looking at graphs (to check that simulation
results are close enough to known targets) with an automatic equivalent.
This requires transforming the manual process into something that can be executed by a computer
and generate an unambiguous verdict on whether or not there is an issue that needs to be investigated.

.. note:: 
  In the future, we could explore having a result more complex than just "investigate" or
  "don't investigate," such as multiple levels of priority for potential issues to be reviewed by a human.
  For now, we stick to a binary result.

For checks where the expectation is that the simulation result should exactly match a target or respect
a constraint (e.g. 0% coverage of an intervention among those not eligible) it is fairly straightforward
to use a Python :code:`assert` statement in addition to, or instead of, a plot.
This approach can be used in notebooks or in automated tests run with :code:`pytest`.

The statistical methodology behind fuzzy checking lives in the
`vivarium-fuzzy-checker documentation
<https://vivarium-fuzzy-checker.readthedocs.io/en/latest/fuzzy_checking.html>`_.

Automated V&V runs
------------------

.. todo::
  Due to technical limitations in the :code:`pytest` tool, integration tests currently must select a simulation
  "size" (population, draws, time span), run it completely, and then check the results.
  It would likely lead to a much quicker iteration cycle if we ran a small simulation, checked the results,
  then added more population/draws/time and checked the results again, etc, similar to how we expand runs with
  :code:`psimulate`.
  This way, egregious bugs could be caught very quickly.

History/case studies
--------------------

Probabilistic record linkage (PRL) synthetic population simulation
++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

The :ref:`PRL synthetic population simulation <vivarium_census_prl_synth_data>`
used an atypical V&V process,
and was used as a testbed for some of these automation ideas.

Rather than researchers performing V&V in notebooks, V&V for this simulation
was entirely done via `integration tests <https://en.wikipedia.org/wiki/Integration_testing>`_,
which ran the simulation for a given number of time steps and checked certain conditions.
When the model exhibited unexpected behavior,
the tests failed.
For example, there was a test that checked that each non-GQ household in the simulation had exactly one
living reference person, a condition which should always be true.

These tests have access to intermediate simulation states, not only simulation outputs, and can check
conditions at each time step.
Therefore, they are quite similar to :ref:`interactive simulation V&V <vivarium_interactive_simulation>` we have done in the past.
Because not much changes over time in the PRL simulation (there is nothing like an intervention scale-up),
we test only the first 10 time steps.

Test coverage was not complete; notably, none of the observers had integration tests.
The test suite can be found `in the simulation repository's integration tests directory <https://github.com/ihmeuw/vivarium_census_prl_synth_pop/tree/main/tests/integration>`_.
The tests all ran in just a few minutes, so engineers were expected to run them before merging any pull request,
though we did not have continuous integration (CI).

These integration tests were the first place we used fuzzy checking,
specifically for rates of simulant migration into and within the US (fuzzy checking was not implemented for emigration).
These rates are stratified by a number
of demographic factors, and some of these factors (e.g. race/ethnicity) have highly imbalanced categories.
Therefore, verifying rates within each demographic combination would require a large population size.

Instead, the integration tests do a combination of verification and validation by checking
**population-level** migration rates against the corresponding rates in our data source (the American Communities Survey).
These should be similar, since the simulation's rates are calculated using this data source,
and the demographic composition of the population is initialized from the same data.
However, simulation rates can drift slightly from population-level rates in the data, without being indicative of a bug,
due to demographic change over the course of the simulation.
All these checks were implemented as proportion checks on the proportion of simulants experiencing the vent.
Checking at the population level makes use of the binomial approximation to the Poisson binomial,
as described in the proportions section.

For rates of migration within the US, we check the migration rate at each time step, and overall.
We set the target range for each time step by assuming with 95% certainty that the drift will be at most 1% per time step that has elapsed
since initialization.
Overall, we set a UI of +/-10% the ACS value.

Migration into the US is a bit different; it is not an event with a rate of occurrence among
an at-risk population.
The only stochastic part of determining the number of immigration events is the
:ref:`"stochastic rounding" used <census_prl_international_immigration>`.
We check this rounding as a set of Bernoulli trials, one per time step:
whether to round up or down.

The PRL integration tests are run very frequently by the software engineering team.
Due to how frequently they are run and the difficulty of debugging a failed test
(perhaps requiring researcher input in some cases),
it is important for these tests to be highly **specific**;
they should very rarely fail by chance.
That is the main reason we have set the default Bayes factor cutoff to 100, commonly called "decisive,"
in *addition* to the generally conservative approximations described above.
In practice, by manually introducing bugs in the simulation, we have found that even with this very conservative approach, automated V&V is quite sensitive.

pseudopeople noise tests
++++++++++++++++++++++++

Our `pseudopeople <https://pseudopeople.readthedocs.io/>`_ package applies random noise to data derived from the PRL synthetic population simulation.
We used fuzzy checking in both the `unit <https://github.com/ihmeuw/pseudopeople/tree/main/tests/unit>`_ and `integration <https://github.com/ihmeuw/pseudopeople/tree/main/tests/integration>`_ tests of pseudopeople to check that
noise was happening at the expected rates.
We did not have any false alarms, and fuzzy checking caught some `very subtle bugs <https://github.com/ihmeuw/pseudopeople/pull/373>`_.
