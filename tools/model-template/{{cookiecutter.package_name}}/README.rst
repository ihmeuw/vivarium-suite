===============================
{{ cookiecutter.package_name }}
===============================

{{ cookiecutter.package_description }}

**Note: It is not yet possible to run this simulation outside of the IHME network.**
This is because it has not yet been "archived," which means that the input data
necessary are only accessible within IHME.
We usually archive a simulation when development is complete.

.. contents::
   :depth: 1

Installation
------------

Open up your normal shell
(if you're on linux or OSX) or the ``git bash`` shell if you're on Windows.
First, clone this repository::

  :~$ git clone https://github.com/ihmeuw/{{ cookiecutter.package_name }}.git
  ...git will copy the repository from github and place it in your home directory...
  :~$ cd {{ cookiecutter.package_name }}

You will need ``conda`` to install all of this repository's requirements.
We recommend installing `Miniforge <https://github.com/conda-forge/miniforge>`_.
The platform-specific instructions for installation can be found at that link.
Once you have ``conda`` installed, you are ready to proceed.

Currently, the process of making artifacts and running simulations requires
two distinct environments.
**Note that it will not be possible to create the environment for making artifacts
unless you are on the IHME network.**
We call these the "artifact" and "simulation" environments.

There are two environment options: a **local conda environment** (for personal
machines) or a **shared environment on the cluster** with a lightweight venv wrapper.

To create or update an environment, use ``source environment.sh``. This will
automatically create the environment if it doesn't exist.

**Local conda environment** (default)::

  :~$ source environment.sh
  ...creates/activates the simulation conda environment...
  :~$ source environment.sh -t artifact
  ...creates/activates the artifact conda environment...

Local conda environments are automatically rebuilt if they are stale (older
than a week). To deactivate a local conda environment, run ``conda deactivate``.

**Shared environment on the cluster** (recommended for cluster development)::

  :~$ source environment.sh -s
  ...creates/activates a venv overlay on the shared simulation environment...
  :~$ source environment.sh -s -t artifact
  ...creates/activates a venv overlay on the shared artifact environment...

To deactivate a shared cluster environment, run ``deactivate``.

The shared environments are conda environments built nightly by Jenkins;
``source environment.sh -s`` layers a lightweight virtual environment on top
of one, with this repository installed in editable mode. Note that this
requires the repository to have been added to the Jenkins shared-environment
nightly build; until then (or if the shared environment is otherwise
unavailable), use the local conda environment instead.

Additional options are available; pass the ``-h`` flag to see them
(e.g. ``-f`` to force a rebuild, ``-l`` to install git lfs).
The underlying ``make`` targets can also be run directly: ``make build-env``
and ``make build-shared-env``; see the ``help`` target in the ``Makefile``
for their arguments.

Supported Python versions: 3.10, 3.11, 3.12

Making Artifacts
----------------

As noted above, it is not possible to make artifacts unless you are on the IHME network.
If you are not on the IHME network, you will be limited to running simulations from pre-made
artifacts; see the next section for how to do this.

In order to make an artifact for a location (e.g. Pakistan), you will first have to add the
location to the ``LOCATIONS`` constant in the ``src/{{ cookiecutter.package_name }}/constants/metadata.py`` file.
Then, you can make the artifact by activating the artifact environment
(``source environment.sh -t artifact``, plus ``-s`` for a shared environment)
and running the following::

  ({{ cookiecutter.package_name }}_artifact) :~$ make_artifacts -vvv -l "Pakistan" -o src/{{ cookiecutter.package_name }}/artifacts

Running Simulations
-------------------

If you've made your own artifact, you will need to update the ``input_data`` section of the ``model_spec.yaml`` file to point to the artifact you want to use as input.
The model specification file is located at ``src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml``.
It is a description of the Vivarium model in a `YAML <https://en.wikipedia.org/wiki/YAML>`__ format.
You can edit this file to modify the simulation that runs.
For more about this, see the documentation at
https://vivarium-engine.readthedocs.io/en/latest/concepts/model_specification/index.html

With the simulation environment active, you can run a single simulation (1 draw, 1 seed, and 1 scenario) by, e.g.::

   ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ simulate run -v src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml

The ``-v`` flag will log verbosely, so you will get log messages every time
step. For more ways to run simulations, see the tutorials at
https://vivarium-engine.readthedocs.io/en/latest/tutorials/running_a_simulation/index.html
and https://vivarium-engine.readthedocs.io/en/latest/tutorials/exploration.html

**If you are on the IHME cluster**, you can also run simulations of multiple draws, seeds, and scenarios in parallel across nodes::

  ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ psimulate run src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml src/{{ cookiecutter.package_name }}/model_specifications/branches/scenarios.yaml

Running Tests
-------------

You can run tests with::

  ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ pytest --runslow
  ...pytest will run all tests in the tests directory...

It may be the case that a different set of tests will run, depending on whether you are in the artifact
or simulation environment.
To be safe, it is best to run the tests in both environments.

Repository Layout
-----------------

The main ``src/{{ cookiecutter.package_name }}`` directory contains all the source code,
while the ``tests`` directory contains all code used for automated testing.