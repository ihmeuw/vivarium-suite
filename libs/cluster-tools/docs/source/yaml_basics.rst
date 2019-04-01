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
preceding token by a space. For example, adding a comment to the configuration from above:

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
configuration that specifies some configuration parameters for a simulation population. Each colon begins a mapping.

.. code-block:: yaml

    configuration:
        population:
            population_size: 1000
            age_start: 0
            age_end: 30

This will interpreted as below. In other words, whitespace indentation is interpreted as nested while a colon is used to
map a key and a value. Also, the inner most block (population_size, age_start, age_end) are unordered.

.. code-block:: yaml

    {configuration: {
        population: {
            population_size : 1000,
            age_start: 0,
            age_end: 30
            }
        }
    }

You may have noticed that the above example contains nested mappings. This is valid YAML syntax.

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

.. code-block:: yaml

    {components: {
            vivarium_public_health: {
                    population : [BasePopulation(), Mortality(), FertilityCrudeBirthRate()]
                    }
            }
    }

Composite Data
--------------

Lists and Mappings can be nested together to make more complicated structures. In fact, A Vivarium model specification
generally takes the form of a set of nested mappings, where some values are lists.
