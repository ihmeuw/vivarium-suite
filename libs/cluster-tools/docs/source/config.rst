
Writing your first model specification
======================================
The ``vivarium`` ecosystem uses YAML configuration files throughout, including model specifications and branch files
that you will be used by ``vivarium-cluster-tools``. Before you start writing your first ``vivarium`` simulation
specification, it is helpful to understand how YAML syntax works.

.. contents::
    :depth: 1
    :local:
    :backlinks: none


YAML Basics
************
YAML is a simple, human-readable data serialization format that is often used for specification files. The extensions
of a file can be **.yaml** or **.yml**, both of which are accepted in ``vivarium``. The following are useful rules
that you should keep in mind when writing a model specification.

1. Whitespace indentation is used to denote structure.

For example, you want to use ``BasePopulation()`` `component` in your simulation. To specify this you need to include it
in your model specification in blocks that denote its import path. Using whitespace indentation, you can show that
``BasePopulation()`` is in the ``population`` module of the ``vivarium_public_health`` library. However,
you should **never use tab** to make your indentation.

.. code-block:: yaml

   components:
        vivarium_public_health:
                population:
                        - BasePopulation()

2.  ``:`` (colon) is used to store data as a map containing keys and values (just like a python ``dictionary``).

There is no order among those key-value pairs and each pair should be formatted as **key: value**.
For example, the following code block shows how you want to specify your initial population in a model specification.
It tells us that you want to have an initial population size of 1000 and ages between 0 and 30.

.. code-block:: yaml

    configuration:
            population:
                    population_size: 1000
                    age_start: 0
                    age_end: 30

This will interpreted as below. In other words, whitespace indentation is interpreted as nested while a colon is used to
map a key and a value. Also, the inner most block (population_size, age_start, age_end) are unordered.

.. code-block:: python

    {configuration: {
            population: {
                population_size : 1000,
                age_start: 0,
                age_end: 30
            }
        }
    }


3. ``-`` (hyphen) is used to denote **Lists**, meaning that the values are not associated with a key.

Just like ``key:value`` pairs, list items are defined in the lines below the list key, all with the **same** amount of
spaces prefixing them. The difference is that all children values begin with a hyphen. For example,

.. code-block:: yaml

    components:
        vivarium_public_health:
            population:
                - BasePopulation()
                - Mortality()
                - FertilityCrudeBirthRate()

This will be interpreted as

.. code-block:: python

    {components: {
            vivarium_public_health: {
                    population : [BasePopulation(), Mortality(), FertilityCrudeBirthRate()]
                    }
            }
    }


Model Specification
*******************

Now, using the basic syntax, let's write a simple ``vivarium`` model specification. As a top level, your
model specification will need three keys: ``plugins``, ``components``, ``configuration``.

1. ``plugins``: As a vivarium user who is using GBD data, you might have seen this on top of a model specification files.

.. code-block:: yaml

    plugins:
            optional:
                    data:
                            controller: "vivarium_public_health.dataset_manager.ArtifactManager"
                            builder_interface: "vivarium_public_health.dataset_manager.ArtifactManagerInterface"

This is required block if your simulation is using a data artifact and you do not need to change this. However,
you can skip it if you only rely on completely data free component like
`this example <https://github.com/ihmeuw/vivarium/blob/develop/src/vivarium/examples/disease_model/disease_model.yaml>`_.
If you just want to load data directly from GBD and not from a data artifact, you should specify the following data
plugins

.. code-block:: yaml

    plugins:
            optional:
                    data:
                            controller: "vivarium_inputs.data_artifact.ArtifactPassthrough"
                            builder_interface: "vivarium_public_health.dataset_manager.ArtifactManagerInterface"

2. ``components``: This block specifies all the basic components that you want to have in a simulation. In general,
it includes population, risk, disease, intervention and any metrics.

- ``population``: You want to have at least ``BasePopulation()`` in your simulation.
  Then, you can also bring mortality and/or making it as an open cohort by adding **one** of three available fertility
  components.

.. code-block:: yaml

    components:
            vivarium_public_health:
                    population:
                            - BasePopulation()
                            - Mortality()
                            - FertilityDeterministic()
                            - FertilityCrudeBirthrate()
                            - FertilityAgeSpecificRates()


- ``risks``: By adding a risk component, you can have your simulants to be exposed to a certain risk.
  However, it does not necessarily mean that they will be affected by risk. To make that connection, you must explicitly
  state how a risk is to affect a specified target.

.. code-block:: yaml

    components:
            vivarium_public_health:
                    risks:
                            - Risk("risk_factor.child_stunting")
                            - Risk("coverage_gap.lack_of_vitamin_a_deficiency")
                            - RiskEffect("risk_factor.child_stunting", "cause.diarrheal_diseases.incidence_rate")
                            - RiskEffect("coverage_gap.lack_of_vitamin_a_deficiency", "risk_factor.vitamin_a_deficiency.exposure_parameter")

- ``diseases``: Disease component often refers a certain type of disease model that you want to include in your
  simulation. Currently we have several standard disease component models, including SI, SIR, SIS, SIS_fixed_duration
  and a neonatal model as well as RiskAttributableDisease (which is a disease defined by a type of risk where the
  population attributable fraction of the disease and risk is 1.)

.. code-block:: yaml

    components:
            vivarium_public_health:
                    disease.models:
                            - SIR_fixed_duration("measles", "10")
                            - SIS("diarrheal_diseases")
                    disease.special_disease:
                            - RiskAttributableDisease("cause.protein_energy_malnutrition", "risk_factor.child_wasting")

- ``intervention``: By adding a treatment plan, you can modify a target measure by implementing your treatment.
  Even though many of intervention components are written in a way to be used for a specific occasion,
  there are still some generic components in ``vivarium_public_health``.

.. code-block:: yaml

    components:
            vivarium_public_health:
                    treatment:
                            - HealthcareAccess()
                            - TherapeuticInertia()

- ``metrics``: Most of time, your output will be the final status of your population at the end of the simulation.
  However, you may wonder what **actually** happened during your simulation and want to have a record of quantities of
  interest as well. as well. For example, you may be interested in the actual risk exposure of simulants at the
  mid-point of each year, by sex and age group. Or, you may want to know the number of deaths due to each cause in your
  simulation by each age group. Or you may want to know the number of vaccines given by age, year, and sex. In all of
  these cases, you can add observers to your model specification file to record these quantities.

.. code-block:: yaml

    components:
            vivarium_public_health:
                    metrics:
                        - MortalityObserver()
                        - TreatmentObserver('shigellosis_vaccine')
                        - Disability()
                        - DiseaseObserver('measles')
                        - CategoricalRiskObserver('risk_factor.vitamin_a_deficiency')





