This directory contains the following five subdirectories:

- ``components``

  This directory is for Python modules containing custom components for
  the {{ cookiecutter.package_name }} project.

- ``constants``

  This directory is for constant values for
  the {{ cookiecutter.package_name }} project.

- ``data``

  If you have **small scale** external data for use in your sim or in your
  results processing, it can live here. This is almost certainly not the right
  place for data, so make sure there's not a better place to put it first.

- ``model_specifications``

  This directory should hold all model specifications and branch files
  associated with the project.

- ``tools``

  This directory hold Python files used to run scripts used to prepare input
  data or process outputs.

When we complete the `archiving process <https://hub.ihme.washington.edu/spaces/SSE/pages/229282235/Archiving+a+Simulation+Model+Repository>`__
we will additionally create the following directory:

- ``artifacts``

  This directory contains all input data used to run the simulations.
  You can open these files and examine the input data using the Vivarium
  artifact tools.
  A tutorial can be found at https://vivarium.readthedocs.io/en/latest/tutorials/artifact.html#reading-data