.. toctree::
   :maxdepth: 2
   :caption: Contents:

Installation
=================


This tool requires access to the IHME cluster and internal PyPI server. To install this package, first create or edit a
file called ~/.pip/pip.conf to contain this entry:
::

   [global]
   extra-index-url = http://dev-tomflem.ihme.washington.edu/simple
   trusted-host = dev-tomflem.ihme.washington.edu

This file tells the pip package management system to include IHME's internal PyPI server when searching for packages.

You can then install this package with
::

    > source activate <your-env-name>
    > pip install vivarium-cluster-tools

In addition, this tool needs the redis client and cython. These must be installed using conda.
::

   > conda install redis cython
