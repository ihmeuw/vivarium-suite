==========================
Vivarium Research Template
==========================

Template for producing research repositories for use with
`vivarium <https://github.com/ihmeuw/vivarium>`_ and
`vivarium_public_health <https://github.com/ihmeuw/vivarium_public_health>_`.

.. contents::
   :depth: 1


Usage
-----

A new github repository can be created using this template by running:

   ``> cookiecutter git@githubcom:ihmeuw/vivarium_research_template.git``

Complete instructions for setting up a new github model repository can be found
`on the hub <https://hub.ihme.washington.edu/display/SSE/Creating+A+New+Model+Repository>`_.

Development
-----------

Things for the ``Vivarium Developers`` to keep an eye on:

- ``model_specifications/{{cookiecutter.package_name}}.yaml``

  Ensure the components and configuration keys supplied are kept up to date.
