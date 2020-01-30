===============================
{{ cookiecutter.package_name }}
===============================

{{ cookiecutter.package_description }}

.. contents::
   :depth: 1

Model Documentation Resources
-----------------------------

**You should put links to the concept model documentation and any other**
**relevant documentation here.**

Installation
------------

These models require data from GBD databases. You'll need several internal
IHME packages and access to the IHME cluster.

To install the extra dependencies create a file called ~/.pip/pip.conf which
looks like this::

    [global]
    extra-index-url = https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/simple/
    trusted-host = artifactory.ihme.washington.edu


To set up a new research environment, open up a terminal on the cluster and
run::

    $> conda create --name={{ cookiecutter.package_name}} python=3.6
    ...standard conda install stuff...
    $> conda activate {{ cookiecutter.package_name }}
    ({{ cookiecutter.package_name }}) $> conda install redis
    ({{ cookiecutter.package_name }}) $> git clone {{ cookiecutter.ssh_url }}
    ...you may need to do username/password stuff here...
    ({{ cookiecutter.package_name }}) $> cd {{ cookiecutter.package_name }}
    ({{ cookiecutter.package_name }}) $> pip install -e .


Usage
-----

You'll find four directories inside the main
``src/{{ cookiecutter.package_name }}`` package directory:

- ``components``

  This directory is for Python modules containing custom components for
  the {{ cookiecutter.package_name }} project. You should work with the
  engineering staff to help scope out what you need and get them built.

- ``data``

  If you have **small scale** external data for use in your sim or in your
  results processing, it can live here. This is almost certainly not the right
  place for data, so make sure there's not a better place to put it first.
  Otherwise, this is the place to put data processing tools and scripts.

- ``model_specifications``

  This directory should hold all model specifications and branch files
  associated with the project.

- ``verification_and_validation``

  Any post-processing and analysis code or notebooks you write should be
  stored in this directory.

