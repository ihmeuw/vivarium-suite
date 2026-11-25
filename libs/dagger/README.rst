===============
Vivarium Dagger
===============

.. image:: https://badge.fury.io/py/vivarium-dagger.svg
    :target: https://badge.fury.io/py/vivarium-dagger

.. image:: https://readthedocs.org/projects/vivarium-dagger/badge/?version=latest
    :target: https://vivarium-dagger.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library builds and runs multi-step data pipelines that you write in Python. A
pipeline definition adds steps to a ``Pipeline``, and each step declares the files it
reads and writes. ``dagger`` connects each step to the steps that write its input files
and runs the steps in order with ``pytask``, either in its own process or as one Jobmon
workflow on a Slurm cluster. ``dagger`` also runs a workflow described in a YAML file
(see `Workflows in a YAML file`_ below).

This version is an alpha that runs every step on every run, as
`What the alpha does <https://vivarium-dagger.readthedocs.io/en/latest/python_pipelines.html#what-the-alpha-does>`_
explains.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-dagger`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-dagger

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/dagger

.. note::

    The ``[cluster]`` extra (``pip install "vivarium-dagger[cluster]"``) pulls in
    ``jobmon_installer_ihme`` from IHME's internal PyPI mirror. ``dagger`` needs it to
    run steps through Jobmon. The ``[simulation]`` extra installs
    ``vivarium-psimulate``, which a YAML workflow with a ``simulation`` step needs.

Python pipelines
----------------

A pipeline definition is a Python file with a function, named ``pipeline`` by default,
that makes a ``Pipeline``, adds steps to it, and returns it. A step names its input
files by the file keys that earlier steps write, and its own output files with
``outputs=``. This definition has one step of each kind:

.. code-block:: python

    from pathlib import Path

    import pandas as pd

    from vivarium.dagger.frontend import Pipeline

    HERE = Path(__file__).resolve().parent
    MODEL_ENV = "my_model_env"  # has papermill, ipykernel, vivarium-psimulate[cluster]


    def make_population() -> pd.DataFrame:
        return pd.DataFrame(
            {"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]}
        )


    def pipeline() -> Pipeline:
        p = Pipeline(HERE / "output", run_name="example")
        p.add_python_step(
            make_population, inputs={}, outputs="population.csv", name="population"
        )
        p.add_command_step(
            "grep -v 0-14 {population} > {adults}",
            inputs={"population": "population.csv"},
            outputs={"adults": "adults.csv"},
            name="adults",
        )
        p.add_simulation_step(
            spec=HERE / "model_spec.yaml",
            branches=HERE / "branches.yaml",
            outputs="sims/model",
            env=MODEL_ENV,
            options={"project": "proj_example", "max_runtime": "24:00:00"},
            # psimulate waits for its whole workflow, so give the step that long.
            resources={"runtime": 26 * 3600, "memory": 4},
        )
        p.add_notebook_step(
            HERE / "report.ipynb",
            executed="executed/report.ipynb",
            env=MODEL_ENV,
            inputs={"adults": "adults.csv", "simulation": "sims/model"},
            outputs={"report": "report.csv"},
            name="report",
        )
        return p

A Python step calls a function in the environment that ``dagger`` runs in. A command
step runs a shell command, a notebook step runs a notebook with papermill, and a
simulation step runs ``psimulate run``. These three can run in another environment,
named by ``env``.

Preview the run, then run every step in this process, or only the steps that a named
step needs:

.. code-block:: console

    dagger preview -c pipeline.py
    dagger run -c pipeline.py --local
    dagger run -c pipeline.py --local --until adults

A simulation step runs ``psimulate``, which works only on a compute node, so a
``--local`` run that includes ``sims/model`` must start in a Slurm allocation; the
``--until adults`` run does not include it.

``dagger force -c pipeline.py --local population`` runs the named steps; in the alpha it
runs the same steps as ``dagger run``. ``dagger show`` prints what each step reads,
writes and runs, and ``dagger graph`` draws the steps and the files between them.

Without ``--local``, ``dagger run`` and ``dagger force`` run each step on a compute node
as a task of one Jobmon workflow. This needs the ``cluster`` extra, and ``dagger`` must
run on a compute node in an allocation that lasts for the whole workflow:

.. code-block:: console

    pip install "vivarium-dagger[cluster]"
    srun -A proj_example -p all.q -c 1 --mem 4G -t 24:00:00 --pty bash
    dagger run -c pipeline.py -P proj_example

`Running a Python pipeline <https://vivarium-dagger.readthedocs.io/en/latest/python_pipelines.html>`_
describes each step method, each command and what happens when a step fails.

Workflows in a YAML file
------------------------

A ``dagger`` workflow file lists steps that run in order. Each step gives its
command and its compute resources:

.. code-block:: yaml

    workflow:
      name: my_pipeline
      project: proj_simscience
      queue: all.q
      output_directory: /path/to/output
      steps:
        - name: run_sims
          type: simulation
          resources:
            memory_gb: 3
            runtime: "24:00:00"
          args:
            model_specification: /path/to/model_specification.yaml
            branch_configuration: /path/to/branches.yaml
        - name: post_process
          command: my_post_processing_script /path/to/output
          resources:
            memory_gb: 8
            runtime: "00:30:00"

Launch the workflow with

.. code-block:: console

    dagger run -c /path/to/workflow.yaml

If a run fails partway through, resume it from its output directory, skipping
steps that already completed, with

.. code-block:: console

    dagger restart /path/to/output

`Check out the docs! <https://vivarium-dagger.readthedocs.io/en/latest/>`_
--------------------------------------------------------------------------
