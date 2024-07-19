===============================
{{ cookiecutter.package_name }}
===============================

{{ cookiecutter.package_description }}

.. contents::
   :depth: 1

Installation
------------

You will need ``conda`` to install all of this repository's requirements.
We recommend installing `Miniforge <https://github.com/conda-forge/miniforge>`_.

Once you have conda installed, you should open up your normal shell
(if you're on linux or OSX) or the ``git bash`` shell if you're on windows.
You'll then make an environment, clone this repository, then install
all necessary requirements as follows::

  :~$ conda create --name={{ cookiecutter.package_name }} python=3.11 git git-lfs
  ...conda will download python and base dependencies...
  :~$ conda activate {{ cookiecutter.package_name }}
  ({{ cookiecutter.package_name }}) :~$ git clone https://github.com/ihmeuw/{{ cookiecutter.package_name }}.git
  ...git will copy the repository from github and place it in your current directory...
  ({{ cookiecutter.package_name }}) :~$ cd {{ cookiecutter.package_name }}
  ({{ cookiecutter.package_name }}) :~$ pip install -e .
  ...pip will install vivarium and other requirements...

Supported Python versions: 3.9, 3.10, 3.11

Note the ``-e`` flag that follows pip install. This will install the python
package in-place, which is important for making the model specifications later.

To install requirements from a provided requirements.txt (e.g. installing an
archived repository with the exact same requirements it was run with), replace
`pip install -e .` with the following::

  ({{ cookiecutter.package_name }}) :~$ pip install -r requirements.txt

Cloning the repository should take a fair bit of time as git must fetch
the data artifact associated with the demo (several GB of data) from the
large file system storage (``git-lfs``). **If your clone works quickly,
you are likely only retrieving the checksum file that github holds onto,
and your simulations will fail.** If you are only retrieving checksum
files you can explicitly pull the data by executing ``git-lfs pull``.

Vivarium uses the Hierarchical Data Format (HDF) as the backing storage
for the data artifacts that supply data to the simulation. You may not have
the needed libraries on your system to interact with these files, and this is
not something that can be specified and installed with the rest of the package's
dependencies via ``pip``. If you encounter HDF5-related errors, you should
install hdf tooling from within your environment like so::

  ({{ cookiecutter.package_name }}) :~$ conda install hdf5

The ``({{ cookiecutter.package_name }})`` that precedes your shell prompt will probably show
up by default, though it may not.  It's just a visual reminder that you
are installing and running things in an isolated programming environment
so it doesn't conflict with other source code and libraries on your
system.


Usage
-----

You'll find six directories inside the main
``src/{{ cookiecutter.package_name }}`` package directory:

- ``artifacts``

  This directory contains all input data used to run the simulations.
  You can open these files and examine the input data using the vivarium
  artifact tools.  A tutorial can be found at https://vivarium.readthedocs.io/en/latest/tutorials/artifact.html#reading-data

- ``components``

  This directory is for Python modules containing custom components for
  the {{ cookiecutter.package_name }} project. You should work with the
  engineering staff to help scope out what you need and get them built.

- ``data``

  If you have **small scale** external data for use in your sim or in your
  results processing, it can live here. This is almost certainly not the right
  place for data, so make sure there's not a better place to put it first.

- ``model_specifications``

  This directory should hold all model specifications and branch files
  associated with the project.

- ``results_processing``

  Any post-processing and analysis code or notebooks you write should be
  stored in this directory.

- ``tools``

  This directory hold Python files used to run scripts used to prepare input
  data or process outputs.


Running Simulations
-------------------

Before running a simulation, you should have a model specification file.
A model specification is a complete description of a vivarium model in
a yaml format.  An example model specification is provided with this repository
in the ``model_specifications`` directory.

With this model specification file and your conda environment active, you can then run simulations by, e.g.::

   ({{ cookiecutter.package_name }}) :~$ simulate run -v /<REPO_INSTALLATION_DIRECTORY>/{{ cookiecutter.package_name }}/src/{{ cookiecutter.package_name }}/model_specifications/model_spec.yaml

The ``-v`` flag will log verbosely, so you will get log messages every time
step. For more ways to run simulations, see the tutorials at
https://vivarium.readthedocs.io/en/latest/tutorials/running_a_simulation/index.html
and https://vivarium.readthedocs.io/en/latest/tutorials/exploration.html
