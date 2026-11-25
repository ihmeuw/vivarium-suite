"""
=======
runtime
=======

The code that runs one step's body, in a local build or on a compute node.
:mod:`~vivarium.dagger.runtime.recipe` holds the JSON recipe that a step crosses to a node
as, :mod:`~vivarium.dagger.runtime.python_run` calls a python step's target and saves
what it returns, and :mod:`~vivarium.dagger.runtime.command` runs a command step's shell
command. None of these modules imports pytask, so the bridge's node runner starts without
it, and only :mod:`~vivarium.dagger.runtime.python_run` imports pandas.

"""

# Import nothing here, and import neither pytask nor the backend or frontend packages in
# any module of this package: the node runner imports recipe and command for every step,
# and python_run for a python step.
