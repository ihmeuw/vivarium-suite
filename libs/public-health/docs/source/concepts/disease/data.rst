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
supplied directly -- as a scalar, ``DataFrame``, or callable -- through the
``data_sources`` configuration without rebuilding an artifact (see the
``Data sources`` section of the :doc:`disease model tutorial
</tutorials/disease>`).

Data keys
---------

The table below lists every data key used by the disease components. Keys marked
**configurable** can be overridden in the ``data_sources`` section of the
configuration; the artifact key shown is simply the default.

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
   * - ``cause.{cause}.cause_specific_mortality_rate``
     - age, sex, year
     - ``value`` (rate)
     - :class:`~vivarium.public_health.disease.model.DiseaseModel`
     - Yes - ``{cause}.data_sources.cause_specific_mortality_rate``

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
