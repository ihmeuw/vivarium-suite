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

.. _fuzzy_checking:

Fuzzy checking
++++++++++++++

It is more difficult to check that a value is correct when the value in the
simulation is subject to stochastic variation.
For example, the mean value of a continuous risk factor will never **exactly** match the GBD value.
The difficulty of this problem is part of why, in the manual V&V process, we usually check such values visually.

Note that fuzzy checking can be applied to both **verification** and **validation**.
For verification, the "target" is that the simulation's value is exactly
correct *with a large enough simulated population*.
For example, if the simulation applies a GBD incidence rate,
then with enough simulants, the simulation should match the GBD rate to many decimal points.
For validation, we specify as a target a 95% uncertainty interval (UI), within which we expect the simulation's **underlying** value (i.e. the value of the simulation result as the simulated population size goes to infinity) should fall 95% of the time.
For example, we could specify that the UI of the simulation's prevalence value is +/-10% of the GBD prevalence, which means it should be 95% certain to be within 10% of GBD **as the simulated population size goes to infinity.**
For more on how to set this UI, see the next sections.

We have formalized fuzzy checking using Bayesian hypothesis tests,
with one test for each of the values to check in the simulation.
In these hypothesis tests, one hypothesis is that the simulation value comes from the target distribution
and the other hypothesis is that it comes from a prior distribution of bugs/issues;
when our data strongly favors the latter, it indicates a problem with the simulation.

V&V as a decision problem
~~~~~~~~~~~~~~~~~~~~~~~~~

The purpose of V&V is to inform the decision of whether to move forward with a simulation as-is,
(e.g., to report its results in a scholarly publication) or investigate the cause of a surprising result.
A surprising result may be valid, or it may be caused by a bug or limitation in the simulation.
Investigating will make us more confident which it is, but it costs us time.

Publishing/using a result is good if the result is accurate, but bad if the result is inaccurate.
How good and how bad these outcomes are, in relation to the cost of investigation and bug-fixing,
depends on the context but is key to making the right V&V decision.

Typically fixing *bugs* (issues caused by differences between documentation and implementation) is
always considered worth the time it takes, which is relatively little
because it does not require redesigning the model or seeking more data.

*Limitations*, on the other hand, are when the documentation was implemented correctly but doesn't
match the real world or expectation.
These are sometimes difficult enough to address and/or have minimal enough
impact on results that we don't fix them even when they are known.
We call such limitations "acceptable."
Some acceptable limitations are known before we even build the model, and we'll call these "planned" limitations.
However, if we failed to accurately anticipate the impact of a planned limitation on our results, that constitutes an additional unplanned
limitation that, when we discover it, we may deem acceptable or unacceptable.

Finding an unplanned but acceptable limitation has some benefit, in that we understand the simulation better, even though we won't change it.
In contexts where this benefit is less than the cost of investigation,
doing an investigation and finding an acceptable limitation is an overall negative,
whereas in other contexts it is worth it.
This impacts how we define our hypotheses, as discussed in the next session.
Furthermore, it may be the case that only *some* unplanned acceptable limitations are worth it to us to know about:
for example, only the unplanned acceptable limitations that are larger than the unplanned limitations we typically
produce in simulation design.

Defining the hypotheses
~~~~~~~~~~~~~~~~~~~~~~~

Our hypotheses are about the simulation **process**, which includes everything starting from primary data collection in the real world,
to data seeking and interpretation, to the modeling itself (since bugs or limitations could be introduced anywhere in this chain).

Our hypotheses are more precisely defined based on the bugs and limitations *that are worth the cost of investigation
to know about*, as discussed in the previous section.
Our "no bug/issue" hypothesis is a distribution representing our subjective belief about the results
that would be generated by a simulation process without any such bugs or limitations.
The "bug/issue" hypothesis is just the opposite: our belief about the results that would be generated
by a simulation process with such bugs or limitations.

Sources of uncertainty
~~~~~~~~~~~~~~~~~~~~~~

For validation (but not verification) fuzzy checks, we will specify uncertainty in our "no bug/issue" hypothesis.
Conceptually, we should only include uncertainty due to parts of the validation data generation process
that are not shared with simulation input data.
We should never include uncertainty due to stochastic
variation in the simulation, as that will be handled by the hypothesis test itself.

For example, if we validate a simulation value against an estimate from a survey that we didn't use to inform
the simulation, those values could be different due to sampling error (including non-response bias) in
the survey, in addition to simulation limitations minor enough that we don't care to investigate them (see previous section).

On the other hand, if we validate a population-level simulation value against an estimate from a survey,
when we used that same survey to inform demographic-specific values for input to the simulation,
the sampling error is shared.
The only reason the simulation value would be different from the survey value is if the demographic
structure of the population is different in the simulation, so we should think about how much we'd
expect it to differ (accounting for data limitations and "minor" simulation limitations in our demographic components)
and set our target uncertainty based only on that.

It will probably not be feasible to do this whole process quantitatively, but understanding
which sources of uncertainty should be included will help us estimate our subjective uncertainty.
Additionally, in practice we do not actually specify an arbitrary distribution for our belief,
but rather a 95% uncertainty interval (UI).
For computational reasons, the distribution actually used is the one closest to replicating that
95% UI from a family pre-selected based on the quantity type (e.g. beta distribution for a proportion).
This is a common limitation in Bayesian hypothesis testing. [Bernardo_2002]_

.. note::
  We treat the 95% UIs as equal-tailed intervals; in other words, we treat the
  lower bound as the 2.5th percentile and the upper bound as the 97.5th.

Choosing the cutoff and population size
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We choose a cutoff `Bayes factor <https://en.wikipedia.org/wiki/Bayes_factor>`_
for each check.
The Bayes factor represents the size of the Bayesian *update* we would make toward
the hypothesis that there is an bug/issue in the simulation.
If this number is greater than our cutoff, the check "fails" and we investigate why the result
is so far from the target.

Increasing the cutoff trades sensitivity for specificity.
The **sensitivity** of a check is the probability of it catching
an issue, given that the issue is present.
The **specificity** is the probability of the check passing when
there is no issue present.
The appropriate tradeoff depends on the cost of investigation (which is wasted by a false alarm)
vs the cost of moving forward with inaccurate results (which is risked by an issue not being flagged).

Increasing the population size trades computational resources for both sensitivity **and** specificity.
A larger population size gives us better power to detect bugs and makes false alarms less likely, at
every cutoff.

Currently, we do not explicitly specify what our sensitivity and specificity values should be.
Instead, we use a conventional "decisive" value of 100 for our Bayes factor cutoff, and set the population
to be as large as we can reasonably run.
We might revisit how to set these values in the future.

You can picture the tradeoffs visually using
this diagram `from Wikipedia <https://en.wikipedia.org/wiki/Sensitivity_and_specificity>`_.

.. figure:: PPV,_NPV,_Sensitivity_and_Specificity.svg

  By Original by Luigi Albert Maria - SVG version of File\:PPV, NPV, Sensitivity and Specificity.pdf, CC BY-SA 4.0, https://commons.wikimedia.org/w/index.php?curid=99283192

The yellow plane represents the cutoff: to the left of this boundary, our check
considers the simulation "Healthy"; to the right, our check
considers the simulation "Sick."

Increasing population size increases both sensitivity and specificity at every cutoff
by making both the blue and red distributions narrower.
Increasing the cutoff increases specificity by cutting off less of the blue distribution
but decreases sensitivity by cutting off more of the red distribution.

In non-fuzzy V&V checks, all "healthy" simulations appear the same, so the blue distribution would be a point mass.
As a result, there are no false alarms, and the specificity is always perfect.
The sensitivity depends on the population size: with a very small simulation run,
there could be lots of buggy situations that are possible but don't occur in that run due to chance.
Fuzzy checking introduces the problem of false alarms ("false positives" in the diagram above), when a check fails randomly without
there being an actual problem in the simulation.

Hypotheses by quantity type
+++++++++++++++++++++++++++

.. todo::
  For now, we have only implemented methods for fuzzy checking proportions.
  Presumably, other types of values could be checked using appropriate hypothesis tests:

  * Summary statistics of continuous values, such as the mean or standard deviation of a hemoglobin distribution
  * Relative risks/rate ratios between categorical groups
  * More complex situations such as the number of unique values of an attribute observed, though these may
    be hard to work out hypotheses for, and are not likely to come up frequently in our simulations.

Proportions
~~~~~~~~~~~

In our discrete-time simulations, on each time step,
a given event happens to some *proportion* of the population at risk.

The proportion we observe in the simulation is the result of some number of independent Bernoulli trials,
one for each simulant at risk.
When the probability of the event is the same for each simulant, the number of events has a binomial distribution.

When simulant-level probabilities vary (for example, if there is a risk factor affecting the probability)
the Bernoulli trials are independent but not identically distributed,
**if we take into account our prior knowledge about the risk factor.**
In that case, the number of events observed has a `Poisson binomial <https://en.wikipedia.org/wiki/Poisson_binomial_distribution>`_
distribution, which has the same mean and **lower** variance than the equal-probability binomial.
Generally, it will be easier for us to ignore our prior knowledge about varying probabilities, and use the binomial distribution.
This sacrifices some sensitivity without a corresponding increase in specificity, because we will
not flag an issue where the result is only very unlikely **given the observed distribution of risk factors.**

When a target 95% UI is specified instead of a single target value,
we fit a `beta distribution <https://en.wikipedia.org/wiki/Beta_distribution>`_ that has approximately that UI.
The lower and upper bounds must both in the interval :math:`(0, 1)`.
Because the beta distribution is the conjugate of the binomial distribution,
we can then use an easy-to-calculate `beta-binomial <https://en.wikipedia.org/wiki/Beta-binomial_distribution>`_ as the distribution
of the number of events.

Finally, we must specify a distribution in the case where there is a bug/error
in the simulation.
For simplicity, we default to a `Jeffreys prior <https://en.wikipedia.org/wiki/Jeffreys_prior>`_ on the probability --
a beta distribution with :math:`\alpha = \beta = 0.5` --
which results in a beta-binomial distribution on the number of events.

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

References
----------

.. [Bernardo_2002] Bernardo, José M., and Raúl Rueda. “Bayesian Hypothesis Testing: A Reference Approach.” International Statistical Review / Revue Internationale de Statistique, vol. 70, no. 3, 2002, pp. 351–72. JSTOR, https://doi.org/10.2307/1403862. Accessed 6 Nov. 2023.