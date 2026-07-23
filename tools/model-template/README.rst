=======================
Vivarium Model Template
=======================

Cookiecutter template for producing research model repositories that use the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ framework
(``vivarium-engine`` + ``vivarium-public-health`` etc.).

.. contents::
   :depth: 1


Usage
-----

Create a new model repository from this template by running:

.. code-block:: bash

   cookiecutter git@github.com:ihmeuw/vivarium-suite.git --directory tools/model-template

Complete instructions for setting up a new model repository can be found
`on the hub <https://hub.ihme.washington.edu/display/SSE/Creating+A+New+Model+Repository>`_.

Development
-----------

To iterate on the template itself:

1. Create a conda environment or virtualenv to isolate your development environment.
2. Install the dependencies:

   .. code-block:: bash

      pip install -r requirements.txt

3. To test out your changes, run the following, where ``<path>`` is the path to
   your local ``tools/model-template/`` directory. That command creates an
   instance of the template in the current working directory.

   .. code-block:: bash

      cookiecutter <path>

Things for the ``Vivarium Developers`` to keep an eye on:

- ``{{cookiecutter.package_name}}/src/{{cookiecutter.package_name}}/model_specifications/model_spec.yaml``

  Ensure the components and configuration keys supplied are kept up to date
  with the current ``vivarium-engine`` / ``vivarium-public-health`` releases.
