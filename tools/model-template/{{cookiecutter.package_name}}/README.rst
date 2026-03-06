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

You will need ``conda`` to install all of this repository's requirements.
We recommend installing `Miniforge <https://github.com/conda-forge/miniforge>`_.
The platform-specific instructions for installation can be found at that link.
Once you have ``conda`` installed, you are ready to proceed.

Currently, the process of making artifacts and running simulations requires
two distinct conda environments.
**Note that it will not be possible to create the environment for making artifacts
unless you are on the IHME network.**
We call these the "artifact" and "simulation" environments.
You'll create these by running::

  :~$ bash environment.sh
  :~$ bash environment.sh -t artifact # only on IHME cluster

You can activate and deactivate the environments like so::

  :~$ conda activate {{ cookiecutter.package_name }}_simulation
  ({{ cookiecutter.package_name }}_simulation) :~$ conda deactivate # note the change in prompt
  :~$ conda activate {{ cookiecutter.package_name }}_artifact
  ({{ cookiecutter.package_name }}_artifact) :~$ conda deactivate # note the change in prompt

The ``({{ cookiecutter.package_name }})`` that precedes your shell prompt will probably show
up by default, though it may not.  It's just a visual reminder that you
are installing and running things in an isolated programming environment
so it doesn't conflict with other source code and libraries on your
system.

Supported Python versions: 3.10, 3.11, 3.12

Making Artifacts
----------------

As noted above, it is not possible to make artifacts unless you are on the IHME network.
If you are not on the IHME network, you will be limited to running simulations from pre-made
artifacts; see the next section for how to do this.

In order to make an artifact for a location (e.g. Pakistan), you will first have to add the
location to the ``LOCATIONS`` constant in the ``src/{{ cookiecutter.package_name }}/constants/metadata.py`` file.
Then, you can make the artifact by activating the artifact conda environment and running the following::

  ({{ cookiecutter.package_name }}_artifact) :~$ make_artifacts -vvv -l "Pakistan" -o src/{{ cookiecutter.package_name }}/artifacts

Running Simulations
-------------------

If you've made your own artifact, you will need to update the ``input_data`` section of the ``model_spec.yaml`` file to point to the artifact you want to use as input.
The model specification file is located at ``src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml``.
It is a description of the Vivarium model in a `YAML <https://en.wikipedia.org/wiki/YAML>`__ format.
You can edit this file to modify the simulation that runs.
For more about this, see the documentation at
https://vivarium.readthedocs.io/en/latest/concepts/model_specification/index.html

With the simulation environment active, you can run a single simulation (1 draw, 1 seed, and 1 scenario) by, e.g.::

   ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ simulate run -v src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml

The ``-v`` flag will log verbosely, so you will get log messages every time
step. For more ways to run simulations, see the tutorials at
https://vivarium.readthedocs.io/en/latest/tutorials/running_a_simulation/index.html
and https://vivarium.readthedocs.io/en/latest/tutorials/exploration.html

**If you are on the IHME cluster**, you can also run simulations of multiple draws, seeds, and scenarios in parallel across nodes::

  ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ psimulate run src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml src/{{ cookiecutter.package_name }}/model_specifications/branches/scenarios.yaml

Running Tests
-------------

You can run tests with::

  ({{ cookiecutter.package_name }}_simulation) :~/{{ cookiecutter.package_name }}$ pytest --runslow
  ...pytest will run all tests in the tests directory...

It may be the case that a different set of tests will run, depending on whether you are in the artifact
or simulation conda environment.
To be safe, it is best to run the tests in both environments.

Repository Layout
-----------------

The main ``src/{{ cookiecutter.package_name }}`` directory contains all the source code,
while the ``tests`` directory contains all code used for automated testing.