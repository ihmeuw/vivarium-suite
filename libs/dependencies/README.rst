=====================
Vivarium Dependencies
=====================

Vivarium Dependencies contains dependency constraints commonly used in Simulation 
Science repositories.

Usage
=====

A downstream repository can use Vivarium Dependencies to define a setup dependency
by including the desired constraint(s) in the `install_requires` dictionary of its setup.py::

  # setup.py
  ...
  if __name__ == "__main__":
    ...
    install_requirements = [
      "vivarium_build_utils[layered_config_tree,pandas]"
      ...
    ]
    ...
    interactive_requirements = ["vivarium_dependencies[interactive]"]
    ...
  ...

Installation
============

You can build ``vivarium_dependencies`` from source with::

  $ git clone https://github.com/ihmeuw/vivarium_dependencies.git
  $ cd vivarium_dependencies
  $ conda create -n ENVIRONMENT_NAME
  $ pip install -e .
