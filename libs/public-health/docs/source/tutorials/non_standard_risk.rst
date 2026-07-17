============================================
Non-standard Risk Exposure and Effect Models
============================================

:mod:`vivarium.public_health` provides three components for modeling the impact
of some health attributes on others:

- :class:`~vivarium.public_health.risks.base_risk.Risk`: Model of the
  underlying exposure based on a continuous or categorical distribution.
- :class:`~vivarium.public_health.risks.effect.RiskEffect`: Model of the
  impact of different exposure levels on another health attribute.
- :class:`~vivarium.public_health.risks.effect.NonLogLinearRiskEffect`: 
  Special-case risk effect model where the risk factors are parameterized
  by exposure levels.

The standard model is to think of exposure to environmental, metabolic, and
behavioral risk factors and their impact on disease incidence rates. However,
we've found many situations to extend this model to other attributes, such as
interventions and their impacts on other risks, diseases, or mortality itself.

In order to support these extended models, we've made the
:class:`~vivarium.public_health.risks.base_risk.Risk`, 
:class:`~vivarium.public_health.risks.effect.RiskEffect`, and
:class:`~vivarium.public_health.risks.effect.NonLogLinearRiskEffect` components
configurable. This tutorial explains the various configuration options you can
use with these components.

.. contents:
   :local:


Data sources
------------

The :class:`~vivarium.public_health.risks.base_risk.Risk` and
:class:`~vivarium.public_health.risks.effect.RiskEffect` components support a
``data_sources`` configuration pattern that lets you override individual data
keys to run without an artifact. Each key defaults to an artifact key but can be
overridden with:

- **Scalar** (int or float) - broadcast a constant value to all simulants.
- **DataFrame** - use the DataFrame directly.
- **Callable** - call the function at setup time to produce the data.
- **Artifact key** (string) - load a different key from the artifact.

Exposure is configured via ``data_sources.exposure`` (see `Exposure Models`_)
and the relative risk via ``data_sources.relative_risk`` (see `Effect Models`_).


Exposure Models
---------------

We model exposure using the
:class:`~vivarium.public_health.risks.base_risk.Risk` or component.
Consider its configuration options:

- ``data_sources.exposure``: The exposure data source. Defaults to the artifact
  key ``<type>.<name>.exposure``. Can be overridden with a scalar, a DataFrame,
  a callable, or a different artifact key.
- ``rebinned_exposed``: Tells the component if a categorical exposure with more
  than two categories should be rebinned into two categories. It defaults to an
  empty list, indicating that the underlying exposure model should be used.
- ``category_thresholds``: This option tells the component how to split
  continuous exposure models into a categorical model. It defaults to an
  empty list, indicating that the underlying exposure model should be used.

The name input when the :class:`~vivarium.public_health.risks.base_risk.Risk`
is created also has an impact on the behavior. Names are provided
as ``<type>.<name>`` where ``type`` refers to the type of entity being
modeled and ``name`` is the name of the entity.  Available types are
``"risk_factor"`` and ``"alternative_risk_factor"``.
Some configuration options are only available for certain entity types, as
summarized in the table below.

.. list-table:: Configuration Options
   :widths: 20 20 20 20
   :header-rows: 1
   :stub-columns: 1
   :align: center

   * -
     - **data_sources.exposure**
     - **rebinned_exposed**
     - **category_thresholds**
   * - **risk_factor**
     - |check_mark|
     - |check_mark|
     - X
   * - **alternative_risk_factor**
     - X
     - X
     - |check_mark|

.. |check_mark| unicode:: U+2713

We'll take each of these entity types one-by-one to see how we can configure
them.


``risk_factor``
+++++++++++++++

For the ``risk_factor`` entity type, both the ``data_sources.exposure`` and
``rebinned_exposed`` configuration options are available to us. In the
model specification, we can specify the component to use its defaults with

.. code-block:: yaml

   components:
       vivarium.public_health:
           risks:
               - Risk("risk_factor.my_risk_factor")

We declare the component but don't declare any configuration options for it.
This will cause the risk component to look up any available exposure
information in the :class:`~vivarium.artifact.artifact.Artifact`
and use the data as presented.

If we set the ``data_sources.exposure`` option to a covariate key as

.. code-block:: yaml

   components:
       vivarium.public_health:
           risks:
               - Risk("risk_factor.my_risk_factor")

   configuration:
       risk_factor.my_risk_factor:
           data_sources:
               exposure: covariate.my_covariate

the component will look for the covariate estimate in the
:class:`~vivarium.artifact.artifact.Artifact` rather than for
the risk factor exposure. Only covariates with a proportion estimate can be
substituted for risk exposure. The covariate proportion will be used as the
proportion of people exposed to the risk factor.

Finally, we can specify an integer or float value to the ``"exposure"`` option
to directly set the proportion of people exposed.

.. code-block:: yaml

   components:
       vivarium.public_health:
           risks:
               - Risk("risk_factor.my_risk_factor")

   configuration:
       risk_factor.my_risk_factor:
           data_sources:
               exposure: 0.6

If the underlying exposure distribution is polytomous (that is, it has
multiple categories of exposure), we can use the ``rebinned_exposed`` option
to separate those categories into an "exposed" and "unexposed" category. The
set of categories to rebin into the "exposed" group should be specified as
a list of strings to the ``rebinned_exposed`` option.

.. code-block:: yaml

   components:
       vivarium.public_health:
           risks:
               - Risk("risk_factor.my_polytomous_risk_factor")

   configuration:
       risk_factor.my_polytomous_risk_factor:
           rebinned_exposed: ["cat1", "cat2", "cat3"]

This will reformat the exposure data to consider anyone in "cat1", "cat2", or
"cat3" as exposed, and all other exposure categories as unexposed.

Using the ``rebinned_exposed`` option will cause the relative risk
for all :class:`~vivarium.public_health.risks.effect.RiskEffect`
components to also be rebinned.

.. note::

   Exposure data is formatted with the typical demographic columns for age,
   sex, location, and year and a value column.  If the exposure data is
   categorical, it also has a "parameter" column with string values of
   "cat1", "cat2", etc.  The categories are presumed to be sorted by severity
   with "cat1" being the worst.


``alternative_risk_factor``
+++++++++++++++++++++++++++

The ``alternative_risk_factor`` is an entity type that indicates we have
both continuous and categorical representations of the exposure. They are used
when an intervention acts on a continuous exposure representation, but the
effects of the exposure are specified in terms of the categorical
exposure representation.

The only relevant configuration option is the ``"category_thresholds"``
option, which **must** be specified. All other keys must be left at their
default values.

.. code-block:: yaml

   components:
       vivarium.public_health:
           risks:
               - Risk("alternative_risk_factor.my_risk_factor")

   configuration:
       alternative_risk_factor.my_risk_factor:
           category_thresholds: [7, 8, 9]


The above configuration would correspond to a risk with a continuous exposure.
Individuals in the simulation would be assigned some actual value in this
distribution (e.g. 7.32 or 9.85).  When calculating effects, individuals
would be assigned a category based on which group they sit in, as defined by
the thresholds in the configuration.  The thresholds here correspond to the
groups ``less than 7``, ``between 7 and 8``, ``between 8 and 9``, and
``more than 9``.  For use in determining effect sizes, these groups will be
labelled ``cat1``, ``cat2``, ``cat3``, and ``cat4`` respectively.


Effect Models
-------------

Non-standard effect models can **only** be used with dichotomous exposure
models (models where someone is either exposed or not exposed). The available
configuration options all correspond to generating a relative risk for
the exposed population from a set of parameters.

We model exposure effects using the
:class:`~vivarium.public_health.risks.effect.RiskEffect` or
:class:`~vivarium.public_health.risks.effect.NonLogLinearRiskEffect` components.

For this tutorial, we'll focus on the ``RiskEffect`` component. The
``NonLogLinearRiskEffect`` component is a special case of the ``RiskEffect``
component where the risk factors are parameterized by exposure levels.

.. todo::
  
   Add details on how to use the ``NonLogLinearRiskEffect`` component.

Let's look at its configuration options:

- ``data_sources.relative_risk``: The relative risk for the exposed group.
  Defaults to the artifact key ``<type>.<name>.relative_risk``. Can be
  overridden with a scalar, a DataFrame, a different artifact key, or the name
  of a ``scipy.stats`` distribution (e.g. ``"norm"``) to draw the relative risk
  from that distribution.
- ``data_source_parameters.relative_risk``: The parameters passed to the
  ``scipy.stats`` distribution named in ``data_sources.relative_risk`` (e.g.
  ``{loc: 2.0, scale: 0.5}`` for ``"norm"``). Ignored unless
  ``data_sources.relative_risk`` is a distribution name.
- ``data_sources.population_attributable_fraction``: The population
  attributable fraction. Defaults to the artifact key
  ``<type>.<name>.population_attributable_fraction``. Supply a scalar (e.g.
  ``0``), a DataFrame, or a different artifact key to override.

When a :class:`~vivarium.public_health.risks.effect.RiskEffect` is created, it
takes two arguments: the name of the exposure model and the name of the
target attribute that should be altered. The exposure model should be named
the same as the argument to :class:`~vivarium.public_health.risks.base_risk.Risk`
and the target attribute should be in the form ``<type>.<name>.<measure>``.
``type`` and ``name`` specify the entity the effect targets and ``measure``
tells the :class:`~vivarium.public_health.risks.effect.RiskEffect` which specific
attribute of the entity to alter. Common targets are exposure for other
:class:`~vivarium.public_health.risks.base_risk.Risk` entities and incidence rates for
diseases.

The Default Case
++++++++++++++++

If we specify no configuration options in the model specification, we end
up with something like:

.. code-block:: yaml

   components:
       vivarium.public_health:
           disease:
               - SIS('my_infectious_disease')
           risks:
               - Risk('risk_factor.my_risk_factor')
               - RiskEffect('risk_factor.my_risk_factor', 'cause.my_infectious_disease.incidence_rate')

In this situation, the :mod:`vivarium.public_health` components will assume
all parameters will come from data.  The
:class:`~vivarium.public_health.disease.models.SIS` component will load measures
like prevalence, incidence rate, excess mortality rate, and others to inform
the initialization and dynamics of the model.  The
:class:`~vivarium.public_health.risks.base_risk.Risk` will load exposure information.
The :class:`~vivarium.public_health.risks.effect.RiskEffect` will load the
population attributable fraction and the relative risk associated with the
risk-cause pair, and link the disease and risk model with this data.

The configuration block for :class:`~vivarium.public_health.risks.effect.RiskEffect`
is specified as

.. code-block:: yaml

   configuration:
       risk_effect.<risk_name>_on_<target>:
           data_sources:
               ...options...

where ``<risk_name>`` is the ``<name>`` provided to the associated
:class:`~vivarium.public_health.risks.base_risk.Risk` component and
``<target>`` is the full target string (``<type>.<name>.<measure>``) passed to
the :class:`~vivarium.public_health.risks.effect.RiskEffect` — e.g.
``risk_effect.my_risk_factor_on_cause.my_infectious_disease.incidence_rate``.

Specifying a Relative Risk Value
++++++++++++++++++++++++++++++++

If you're in a situation where the size of the effect (the relative risk)
between an exposure model and its target outcome are unknown, one option
is to specify a single value for the relative risk.

.. code-block:: yaml

   components:
       vivarium.public_health:
           disease:
               - SIS('my_infectious_disease')
           risks:
               - Risk('risk_factor.my_risk_factor')
               - RiskEffect('risk_factor.my_risk_factor', 'cause.my_infectious_disease.incidence_rate')

   configuration:
       risk_effect.my_risk_factor_on_cause.my_infectious_disease.incidence_rate:
           data_sources:
               relative_risk: 20

For this to work, the exposure modeled by the
:class:`~vivarium.public_health.risks.base_risk.Risk` must be a dichotomous exposure
(only exposed or not exposed).  The ``"relative_risk"`` option provided will
be assigned and used for the exposed group.  Specifying a relative risk
this way will cause the population attributable fraction to be calculated
using the provided exposure model, and so it does not need to be provided.

Specifying a Relative Risk Distribution
+++++++++++++++++++++++++++++++++++++++

If you have some idea of the uncertainty in the relative risk, you can draw it
from a distribution instead of fixing a single value. Set
``data_sources.relative_risk`` to the name of any ``scipy.stats`` distribution
and supply its parameters under ``data_source_parameters.relative_risk``. A
single relative risk is drawn per simulation (the distribution's inverse CDF
evaluated at a seeded random quantile), so the draw is reproducible for a given
random seed.

For example, to draw from a normal distribution with mean two and standard
deviation ``0.5``:

.. code-block:: yaml

   components:
       vivarium.public_health:
           disease:
               - SIS('my_infectious_disease')
           risks:
               - Risk('risk_factor.my_risk_factor')
               - RiskEffect('risk_factor.my_risk_factor', 'cause.my_infectious_disease.incidence_rate')

   configuration:
       risk_effect.my_risk_factor_on_cause.my_infectious_disease.incidence_rate:
           data_sources:
               relative_risk: "norm"
           data_source_parameters:
               relative_risk:
                   loc: 2.0
                   scale: 0.5

Any continuous ``scipy.stats`` distribution can be used (e.g. ``"norm"``,
``"lognorm"``); the keys under ``data_source_parameters.relative_risk`` are
passed straight to that distribution, so consult the ``scipy.stats``
documentation for each distribution's parameterization.

.. note::

   The parameterized :class:`~vivarium.public_health.risks.effect.RiskEffect` can
   be used with a parameterized version of the
   :class:`vivarium.public_health.risks.base_risk.Risk`.  The only requirement
   for use is that exposure model be dichotomous.
