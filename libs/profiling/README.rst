==================
vivarium.profiling
==================

Profiling and benchmarking tools for Vivarium simulations.

.. contents::
   :depth: 1

Installation
------------

``vivarium-profiling`` is published on PyPI as part of the vivarium-suite monorepo:

.. code-block:: bash

   pip install vivarium-profiling

For local development against the monorepo source, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.

Supported Python versions: 3.10, 3.11

**HDF5 backing storage.** Vivarium uses the Hierarchical Data Format (HDF) for its
data artifacts, and the libraries pip needs to read these files may not be present
on your system. If you encounter HDF5-related errors, install the system tooling
into your conda env:

.. code-block:: bash

   conda install hdf5

**git-lfs and data artifacts.** When cloning the monorepo, large data artifacts
are stored via ``git-lfs``. A clone that completes very quickly likely fetched
only the checksum files rather than the artifacts themselves, and your simulations
will fail. If you suspect this happened, pull the data explicitly:

.. code-block:: bash

   git-lfs pull

Source layout
-------------

The package lives at ``src/vivarium/profiling/`` and provides these subpackages:

- ``components`` - custom Vivarium components used by the profiling models
- ``constants`` - project-level constants and metadata
- ``data`` - artifact builder helpers
- ``model_specifications`` - model spec YAMLs (e.g. ``model_spec_scaling.yaml``)
- ``plugins`` - the ``MultiComponentParser`` plugin
- ``templates`` - Jupyter notebook templates for analysis
- ``tools`` - the CLI entry points (``profile_sim``, ``run_benchmark``, ``summarize``,
  ``make_artifacts``)


Profiling and Benchmarking
---------------------------

This repository provides tools for profiling and benchmarking Vivarium simulations
to analyze their performance characteristics. See the tutorials at
https://vivarium.readthedocs.io/en/latest/tutorials/running_a_simulation/index.html
and https://vivarium.readthedocs.io/en/latest/tutorials/exploration.html for general instructions
on running simulations with Vivarium.

Configuring Scaling Simulations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This repository includes a custom ``MultiComponentParser`` plugin that allows you to
easily create scaling simulations by defining multiple instances of diseases and risks
using a simplified YAML syntax.

To use the parser, add it to your model specification::

    plugins:
        required:
            component_configuration_parser:
                controller: "vivarium.profiling.plugins.parser.MultiComponentParser"

Then use the ``causes`` and ``risks`` multi-config blocks:

**Causes Configuration**

Define multiple disease instances with automatic numbering::

    components:
        causes:
            lower_respiratory_infections:
                number: 4          # Creates 4 disease instances
                duration: 28       # Disease duration in days
                observers: True    # Auto-create DiseaseObserver components

This creates components named ``lower_respiratory_infections_1``,
``lower_respiratory_infections_2``, etc., each with its own observer if enabled.

**Risks Configuration**

Define multiple risk instances and their effects on causes::

    components:
        risks:
            high_systolic_blood_pressure:
                number: 2
                observers: False    # Set False for continuous risks
                affected_causes:
                    lower_respiratory_infections:
                        effect_type: nonloglinear
                        measure: incidence_rate
                        number: 2   # Affects first 2 LRI instances

            unsafe_water_source:
                number: 2
                observers: True     # Set True for categorical risks
                affected_causes:
                    lower_respiratory_infections:
                        effect_type: loglinear
                        number: 2

See ``model_specifications/model_spec_scaling.yaml`` for a complete working example
of a scaling simulation configuration.


Running Benchmark Simulations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``profile_sim`` command profiles runtime and memory usage for a single simulation of a vivarium model
given a model specification file. The underlying simulation model can
be any vivarium-based model, including the aforementioned scaling simulations as well as models in a 
separate repository. This will generate, in addition to the standard simulation outputs, profiling data 
depending on the profiler backend provided. By default, runtime profiling is performed with ``cProfile``, but 
you can also use ``scalene`` for more detailed call stack analysis.

The ``run_benchmark`` command runs multiple iterations of one or more model specification, in order to compare
the results. It requires at least one baseline model for comparison,
and any other number of 'experiment' models to benchmark against the baseline, which can be passed via glob patterns.
You can separately configure the sample size of runs for the baseline and experiment models. The command aggregates
the profiling results and generates summary statistics and visualizations for a default set of important function calls
to help identify performance bottlenecks.

The command creates a timestamped directory containing:

- ``benchmark_results.csv``: Raw profiling data for each run
- ``summary.csv``: Aggregated statistics (automatically generated)
- ``performance_analysis.png``: Performance charts (automatically generated)
- Additional analysis plots for runtime phases and bottlenecks


Analyzing Benchmark Results
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``summarize`` command processes benchmark results and creates visualizations.
This runs automatically after ``run_benchmark``, but can also be run manually
for custom analysis after the fact.

By default, this creates the following files in the specified output directory:

- ``summary.csv``: Aggregated statistics with mean, median, std, min, max
  for all metrics, plus percent differences from baseline
- ``performance_analysis.png``: Runtime and memory usage comparison charts
- ``runtime_analysis_*.png``: Individual phase runtime charts (setup, run, etc.)
- ``bottleneck_fraction_*.png``: Bottleneck fraction scaling analysis

You can also generate an interactive Jupyter notebook including the same default plots
and summary dataframe with a ``--nb`` flag, in which case the command also creates an
``analysis.ipynb`` file in the output directory.


Customizing Result Extraction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, the benchmarking tools extract standard profiling metrics:

- Simulation phases: setup, initialize_simulants, run, finalize, report
- Common bottlenecks: gather_results, pipeline calls, population views
- Memory usage and total runtime

You can customize which metrics to extract by creating an extraction config YAML file.
See ``extraction_config_example.yaml`` for a complete annotated example.

**Basic Pattern Structure**::

    patterns:
      - name: my_function          # Logical name for the metric
        filename: my_module.py     # Source file containing the function
        function_name: my_function # Function name to match
        extract_cumtime: true      # Extract cumulative time (default: true)
        extract_percall: false     # Extract time per call (default: false)
        extract_ncalls: false      # Extract number of calls (default: false)

In turn, this yaml can be passed to the ``run_benchmark`` and ``summarize`` commands
using the ``--extraction_config`` flag. ``summarize`` will automatically create runtime
analysis plots for the specified functions. 