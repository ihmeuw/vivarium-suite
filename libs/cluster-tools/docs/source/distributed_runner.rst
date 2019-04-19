.. toctree::
   :maxdepth: 2
   :caption: Contents:

Running simulations in parallel
===============================

Once you successfully create a simulation specification and branch file it is
time to use the distributed runner. Recall, we ran a single simulation with
a model specification file in this way

.. code-block:: console

    simulate run /path/to/your/model/specification

Very similar to this, ``vivarium-cluster-tools`` includes a command for simulating in parallel

.. code-block:: console

    psimulate run /path/to/your/model/specification  /path/to/your/branch

By default, output will be saved in ``/share/costeffectiveness/results``. If you want to save the
results somewhere else you can specify your output directory as an optional argument

.. code-block:: console

    psimulate run /path/to/your/model/specification /path/to/your/branch -o /path/to/output

Another optional argument is the cluster project under which to run the simulations. By default, the cluster project
used is ``proj_cost_effect``. To use a different project, specify it with the ``-P`` flag

.. code-block:: console

    psimulate run /path/to/your/model/specification /path/to/your/branch -P proj_csu

Currently, the projects that simulation science has access to are ``proj_cost_effect``, ``proj_cost_effect_diarrhea``,
``proj_cost_effect_dcpn``, ``proj_cost_effect_conic``, and ``proj_csu``. Only these projects may be used.

If your ``psimulate run`` has failed to complete you can restart the failed jobs by specifying which output directory
includes the partially completed jobs using ``restart``

.. code-block:: console

    psimulate restart /path/to/the/previous/results/

For ``psimulate restart`` you can also choose a project with optional flag ``-P``.


If you wish to expand a previous ``psimulate run`` by adding additional input draws and/or random seeds, you can do so
using ``expand``.

.. code-block:: console

    psimulate expand /path/to/the/previous/results/ --add-draws 10 --add-seeds 5

You can use one or both of ``--add-draws`` and ``--add-seeds`` to expand your simulation. Any previous results will not
be overwritten, but any additional simulations resulting from the new input draws and/or random seeds will be run.

``psimulate expand`` also supports choosing a project via the option flag ``-P``.