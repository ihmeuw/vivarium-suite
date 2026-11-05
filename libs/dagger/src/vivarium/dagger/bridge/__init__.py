"""
======
bridge
======

Run pipeline steps on compute nodes. :mod:`~vivarium.dagger.bridge.payload` turns a step
into a JSON recipe on the controller, and :mod:`~vivarium.dagger.bridge.runner` runs that
recipe on a node and writes a JSON result. A build that sets ``jobmon=True`` runs its stale
steps this way as one Jobmon workflow: :mod:`~vivarium.dagger.bridge.plugin` describes the
build's options, and :mod:`~vivarium.dagger.bridge.execute` describes what the build does.
When the workflow ends, the build records each step's result in pytask's lockfile as a
local build would, and reports a step that failed on its node with
:class:`~vivarium.dagger.bridge.execute.RemoteTaskFailed`. Every other step of the plan is
then settled, so each one gets a report, and :mod:`~vivarium.dagger.bridge.state` keeps
each run's Jobmon workflow id and outcome, so a stopped run can be found.

"""

# Import nothing here. A package __init__ that imports the runner makes
# ``python -m vivarium.dagger.bridge.runner`` run a second copy of the module, and the
# Jobmon adapter must load only when a build uses it, so the runner works without Jobmon.
