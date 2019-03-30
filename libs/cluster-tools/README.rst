Vivarium Cluster Tools
=======================

.. image:: https://badge.fury.io/py/vivarium_cluster_tools.svg
    :target: https://badge.fury.io/py/vivarium_cluster_tools

.. image:: https://travis-ci.org/ihmeuw/vivarium_cluster_tools.svg?branch=master
    :target: https://travis-ci.org/ihmeuw/vivarium_cluster_tools
    :alt: Latest Version

.. image:: https://readthedocs.org/projects/vivarium_cluster_tools/badge/?version=latest
    :target: https://vivarium_cluster_tools.readthedocs.io/en/latest/?badge=latest
    :alt: Latest Docs

This package includes a tool that allows vivarium users to run simulations based on multiple scenarios conveniently.
It requires an access to IHME cluster. To install this package, create or edit a file called ~/.pip/pip.conf which looks like this:

    | [global]
    | extra-index-url = http://dev-tomflem.ihme.washington.edu/simple
    | trusted-host = dev-tomflem.ihme.washington.edu


This file tells the pip package management system to check with IHME's internal
pypi server for packages.

You can then install this package with

    | > source activate <env-name>
    | > pip install vivarium-cluster-tools

In addition, this tool needs redis client and cython.

    | > conda install redis cython

How to use
-------------

The Vivarium ecosystem uses YAML configuration files throughout, including vivarium-cluster-tools.
To run the multiple scenarios, you need to make a separate branch file (YAML) in addition to a usual
vivarium model specification YAML file. You can specify how to vary a certain parameter in this branch file.
For example,

::

    input_draw_count: 25
    random_seed_count: 10

    branches:
      - intervention_name:
           intervention_coverage: [0, 1]
           efficacy: [0, .5, 1]

This branch shows that you want to have 25 different input draws, 10 different random seeds and 2 different
intervention coverages and 3 different intervention efficacy. This implies 25*10*2*3=1500 different simulation
scenarios. You can run all 1500 simulations by

::

    psimulate run /path/to/your/model_configuration /path/to/your/branch_file

As an optional parameter, you can specify the name of project as well as result directory to save the outputs
and logs. To specify those optional arguments,

::

    psimulate run /path/to/your/model_configuration /path/to/your/branch_file -p project_name -o /path/to/output/

If your psimulate run has any failed jobs from the previous runs, you can restart failed jobs by specifying
which output directory includes the partially completed jobs by,

::

    psimulate restart /path/to/the/previous/results/
