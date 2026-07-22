.. _disease_data_concept:

============
Disease Data
============

.. contents::
   :depth: 2
   :local:
   :backlinks: none

The disease components read their input data by **key** from the simulation's
data source (an artifact by default). This page documents the key name and
column layout each component expects. Every configurable key can also be
supplied directly as a scalar, ``DataFrame``, or callable through the
``data_sources`` configuration without rebuilding an artifact (see the
``Data sources`` section of the :doc:`disease model tutorial
</tutorials/disease>`).

Data keys
---------

The table below lists every data key used by the disease components. Keys marked
**configurable** can be overridden in the ``data_sources`` section of the
configuration; the artifact key shown is the default unless the row notes
otherwise.

.. list-table::
   :header-rows: 1

   * - Key
     - Index columns
     - Value columns
     - Used by
     - Configurable?
   * - ``cause.{cause}.prevalence``
     - age, sex, year
     - ``value`` (fraction)
     - :class:`~vivarium.public_health.disease.state.DiseaseState`
     - Yes - ``{state}.data_sources.prevalence``
   * - ``cause.{cause}.birth_prevalence``
     - age, sex, year
     - ``value`` (fraction)
     - :class:`~vivarium.public_health.disease.state.DiseaseState` (neonatal models)
     - Yes - ``{state}.data_sources.birth_prevalence``
   * - *(no artifact key; default* ``0.0`` *)*
     - age, sex, year (or scalar)
     - ``value`` (days)
     - :class:`~vivarium.public_health.disease.state.DiseaseState`
     - Yes - ``{state}.data_sources.dwell_time``
   * - ``cause.{cause}.disability_weight``
     - age, sex, year (or single row)
     - ``value`` (weight)
     - :class:`~vivarium.public_health.disease.state.DiseaseState`
     - Yes - ``{state}.data_sources.disability_weight``
   * - ``cause.{cause}.excess_mortality_rate``
     - age, sex, year
     - ``value`` (rate)
     - :class:`~vivarium.public_health.disease.state.DiseaseState`
     - Yes - ``{state}.data_sources.excess_mortality_rate``
   * - ``cause.{cause}.incidence_rate``
     - age, sex, year
     - ``value`` (rate)
     - :class:`~vivarium.public_health.disease.transition.RateTransition` (from
       susceptible state)
     - Yes - ``{transition}.data_sources.transition_rate``
   * - ``cause.{cause}.remission_rate``
     - age, sex, year
     - ``value`` (rate)
     - :class:`~vivarium.public_health.disease.transition.RateTransition` (from
       infected state)
     - Yes - ``{transition}.data_sources.transition_rate``
   * - *(no artifact key; supplied directly)*
     - age, sex, year (or scalar)
     - ``value`` (proportion)
     - :class:`~vivarium.public_health.disease.transition.ProportionTransition`
     - Yes - ``{transition}.data_sources.proportion``
   * - ``cause.{cause}.cause_specific_mortality_rate``
     - age, sex, year
     - ``value`` (rate)
     - :class:`~vivarium.public_health.disease.model.DiseaseModel`
     - Yes - ``disease_model.{cause}.data_sources.cause_specific_mortality_rate``

``birth_prevalence`` and ``dwell_time`` have no artifact key of their own: each
defaults to the scalar ``0.0`` and is loaded from an artifact only when a key is
supplied (the neonatal factory models pass ``cause.{cause}.birth_prevalence``).
``proportion`` likewise has no default artifact key and is supplied directly.

Transition names
----------------

The ``data_sources`` key for a rate transition is the transition component's
*name*, which ``vivarium`` generates automatically from the transition's
endpoints and rate, so it is more involved than the ``disease_state.{cause}``
and ``disease_model.{cause}`` keys used for states and the model. For a
:class:`~vivarium.public_health.disease.transition.RateTransition` the pattern
is::

    rate_transition.'{source_state_name}'.'{sink_state_name}'.{rate_key}.{rate_type}

where ``{source_state_name}`` and ``{sink_state_name}`` are the component names
of the two states (e.g. ``susceptible_state.susceptible_to_{cause}``,
``disease_state.{cause}``, ``recovered_state.recovered_from_{cause}``),
``{rate_key}`` is the rate's default artifact key (used when no rate is supplied
at construction), and ``{rate_type}`` is ``incidence_rate``, ``remission_rate``,
or ``transition_rate``. An SI model's susceptible-to-infected transition for
cause ``diarrheal_diseases`` is therefore::

    rate_transition.'susceptible_state.susceptible_to_diarrheal_diseases'.'disease_state.diarrheal_diseases'.cause.diarrheal_diseases.incidence_rate.incidence_rate

Because these names are long and easy to mistype, the most reliable way to find
one is to build the model and read each rate transition's ``name`` attribute:

.. code-block:: python

    from vivarium.public_health.disease.models import SI
    from vivarium.public_health.disease.transition import RateTransition

    model = SI("diarrheal_diseases")
    for state in model.states:
        for transition in state.transition_set.transitions:
            if isinstance(transition, RateTransition):
                print(transition.name)

Use the printed string verbatim as the configuration key:

.. code-block:: yaml

    configuration:
        "rate_transition.'susceptible_state.susceptible_to_diarrheal_diseases'.'disease_state.diarrheal_diseases'.cause.diarrheal_diseases.incidence_rate.incidence_rate":
            data_sources:
                transition_rate: 0.5

Data shapes
-----------

Most cause-level measures share the same column layout: one row per
age × sex × year combination with a ``value`` column. For example,
``cause.{cause}.prevalence`` (the fraction of the population in the disease
state) looks like:

.. code-block:: text

    age_start   age_end    sex  year_start  year_end  value
     0.000000  0.019178   Male        1990      1991   0.05
     0.000000  0.019178 Female        1990      1991   0.05
     0.019178  0.076712   Male        1990      1991   0.05
     0.019178  0.076712 Female        1990      1991   0.05
     0.076712  1.000000   Male        1990      1991   0.05
     0.076712  1.000000 Female        1990      1991   0.05

``incidence_rate``, ``remission_rate``, ``excess_mortality_rate``, and
``cause_specific_mortality_rate`` share this layout, with a rate in the
``value`` column. A production artifact has the same columns but with real
GBD values.

``disability_weight`` may instead be a single-row ``DataFrame`` (a constant
weight applied to all simulants in the state):

.. code-block:: text

    value
      0.1

and ``cause.{cause}.restrictions`` is a dict, e.g. ``{'yld_only': False}``.
