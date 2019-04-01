===========
YAML Basics
===========

.. contents::
   :depth: 1
   :local:
   :backlinks: none

YAML is a simple, human-readable data serialization format that is often used for specification files. The extensions
of a file can be **.yaml** or **.yml**, both of which are accepted throughout the Vivarium framework.  The following
are general rules to keep in mind when writing and interpreting Vivarium YAML files. Examples use snippets from Vivarium
model configurations but do not go in-depth about that topic. For more information about model configurations, please
see that section of this documentation.

Structure
---------

YAML files are structured by lines and space indentations. Indentation levels should be either 2 or 4 spaces, and
**tabs are not valid**.  For example, a configuration file that includes a ``BasePopulation()`` component would look
like the following:

.. code-block:: yaml

    components:
        vivarium_public_health:
            population:
                - BasePopulation()

Comments
--------

YAML comments are denoted with the pound symbol ``#``, and can be placed anywhere, but must be separated from the
preceding token by a space. For example, adding a comment to the configuration from above looks like this:

.. code-block:: yaml

    components:
        vivarium_public_health:
            population:
                - BasePopulation()  # Produces and ages simulants, no death


Mappings
--------

A mapping, or key-value pairing, is formed using a colon `:`. This corresponds to an entry from the ``dictionary``
data structure from python, and there is no notion of ordering. Mappings can be specified in block format or inline,
however we recommend block format so that is what we will show an example of here. In block format, mappings are
separated onto new lines, and indentation forms a parent-child relationship. For example, below is a snippet from a
configuration that specifies configuration parameters for a simulation population as mappings. Each colon below begins
a mapping.

.. code-block:: yaml

    configuration:
        population:
            population_size: 1000
            age_start: 0
            age_end: 30

This will interpreted as below.
You may have noticed that the above example contains nested mappings, this is valid YAML syntax. The nesting relies on
whitespace indentation. Also, the inner most block (population_size, age_start, age_end) is unordered.

.. code-block:: yaml

    {configuration: {
        population: {
            population_size : 1000,
            age_start: 0,
            age_end: 30
            }
        }
    }

Lists
-----

A list is formed using a hyphen ``-`` in block format, with each entry appearing on a new line with the same indentation
level.  As with mappings, lists can be nested, and mappings and lists can be intermixed. Below is a configuration
snippet that specifies a list of components to be used from the ``population`` module in Vivarium Public Health.

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

Sometimes, you will see lists specified inline in a format that looks just like a ``python`` list. A common place for
these is in branches configuration files when specifying varying parameters. For more information, see the branches
section of this documentation. An example here is the value of the proportion key below:

.. code-block:: yaml

    input_draw_count: 100
    random_seed_count: 1

    branches:
      - egg_intervention:
            recruitment:
                proportion: [0.0, 0.8]

Composite Data
--------------

Lists and Mappings can be nested together to make more complicated structures. In fact, A Vivarium model specification
generally takes the form of a set of nested mappings, where some values are lists.
