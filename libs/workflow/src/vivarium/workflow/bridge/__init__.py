"""
======
bridge
======

Run pipeline steps on compute nodes. :mod:`~vivarium.workflow.bridge.payload` turns a step
into a JSON recipe on the controller, and :mod:`~vivarium.workflow.bridge.runner` runs that
recipe on a node and writes a JSON result. A build that sets ``jobmon=True`` runs its stale
steps this way as one Jobmon workflow: :mod:`~vivarium.workflow.bridge.plugin` describes the
build's options, and :mod:`~vivarium.workflow.bridge.execute` describes what the build does.

"""

# Import nothing here. A package __init__ that imports the runner makes
# ``python -m vivarium.workflow.bridge.runner`` run a second copy of the module, and the
# Jobmon adapter must load only when a build uses it, so the runner works without Jobmon.
