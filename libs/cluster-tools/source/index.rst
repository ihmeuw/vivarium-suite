.. vivarium_cluster_tools documentation master file, created by
   sphinx-quickstart on Tue Feb 26 11:21:56 2019.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.



Vivarium Cluster Tools
==================================================
.. toctree::
   :maxdepth: 1
   :caption: Contents:

This is the documentation for ``vivarium_cluster_tools`` used to run simulations using ``vivarium``. Current version 1.0.0, it has a distributed runner to make large number of simulations based on different parameters easier. As more tools are included, the documentation will be updated accordingly.

################
Installation
################


This tool requires an access to IHME clutser and internal pypi server. To install this package, create or edit a file called ~/.pip/pip.conf which looks like this
::

   [global]
   extra-index-url = http://dev-tomflem.ihme.washington.edu/simple
   trusted-host = dev-tomflem.ihme.washington.edu

This file tells the pip package management system to check with IHME's internal pypi server for packages.

You can then install this package with
::
    > source activate <your-env-name>
    > pip install vivarium-cluster-tools

In addition, this tool needs redis client and cython.
::
   > conda install redis cython


#############################
How to use distributed runner
#############################
The ``vivarium`` ecosystem uses YAML configuration files throughout, including ``vivarium-cluster-tools``.
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

