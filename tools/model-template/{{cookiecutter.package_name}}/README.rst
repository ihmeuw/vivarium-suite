{{ cookiecutter.package_name }}
{{ cookiecutter.package_name|count * "=" }}

{% if 'readme_pypi_badge' in cookiecutter %}
.. image:: https://img.shields.io/pypi/v/{{ cookiecutter.package_name }}.svg
    :target: https://pypi.python.org/pypi/{{ cookiecutter.package_name }}
    :alt: Latest PyPI version
{%- endif %}

{% if 'readme_travis_badge' in cookiecutter %}
.. image:: {{ cookiecutter.readme_travis_url }}.png
   :target: {{ cookiecutter.readme_travis_url }}
   :alt: Latest Travis CI build status
{%- endif %}

{{ cookiecutter.package_description }}

Usage
-----

Installation
------------

These models require data from GBD databases. You'll need several internal IHME packages and access to the IHME cluster.

To install the extra dependencies create a file called ~/.pip/pip.conf which looks like this:

.. code-block:: none

    [global]
    extra-index-url = http://pypi.services.ihme.washington.edu/simple
    trusted-host = pypi.services.ihme.washington.edu


To set up a new research environment, open up a terminal on the cluster and run:

.. code-block:: console

    $> conda create --name={{ cookiecutter.package_name}} python=3.6
    ...standard conda install stuff...
    $> conda activate {{ cookiecutter.package_name }}
    ({{ cookiecutter.package_name }}) $> conda install redis cython
    ({{ cookiecutter.package_name }}) $> git clone {{ cookiecutter.package_url }}
    ...you may need to do username/password stuff here...
    ({{ cookiecutter.package_name }}) $> cd neonatal
    ({{ cookiecutter.package_name }}) $> pip install -e .
