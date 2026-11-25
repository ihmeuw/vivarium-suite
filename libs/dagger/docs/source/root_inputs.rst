=============================
Root inputs and input helpers
=============================

A pipeline often starts from files that no step produces, such as a coverage table
that an analyst delivers for a new country. This page first describes how any such
file reaches a step, through the pipeline's root inputs. It then describes input
helpers, functions that build root inputs for one convention of where files live.
Last, it describes :func:`~vivarium.dagger.frontend.root_inputs.location_inputs`, the
input helper that ``dagger`` provides for files delivered per location.

Root inputs
-----------

The ``root_inputs`` of a :class:`~vivarium.dagger.frontend.builder.Pipeline` is a
:data:`~vivarium.dagger.frontend.root_inputs.RootInputs` mapping from file key to path.
A step reads one of these files by naming its key as an input, and the step's function
receives the file's path. The mapping can come from anywhere: you can write it out by
hand, call an input helper, or merge several mappings into one.

``dagger`` records each path when the definition's function makes the ``Pipeline``,
and resolves a relative path against the directory ``dagger`` runs from. When a step
that reads a root input is added, ``dagger`` checks only that something exists at the
path. If nothing does, the definition fails to load with an error that names the step
and says ``input '<key>' is unresolved: declared as a root input but no file at
<path>.`` A root input that no step reads is not checked. ``dagger`` does not read the
file when the definition loads, so any check of its format or columns is the job of
the function that returns the mapping. In the alpha every step runs on every build
(see :ref:`what_the_alpha_does`); once a step can be current, a change to a root input's
contents will make the steps that read it stale. A step cannot produce a key that is a
root input, or an output at a root input's file or at a folder that holds one, so that
no step's declared output can overwrite or replace a root input; the ``Pipeline``
refuses such an output, also for a task added with
:meth:`~vivarium.dagger.frontend.builder.Pipeline.add_task`. A command can still write
files that it does not declare, so this does not stop a command from changing a root
input.

Call any function that returns the mapping inside the definition's ``pipeline()``
function, not at the top level of the definition's module. In a run through Jobmon, the
compute node imports a Python step's function from its module. When that function is
defined in the definition file, the file's top-level code runs again on the compute
node of each such step, so a check placed there runs once for each step and fails on a
node that cannot see the files.

Writing an input helper
-----------------------

A model may have conventions for where certain kinds of input files live, based on
parameters such as location. An input helper is any function that returns a
``RootInputs`` for one such convention. By convention, an input helper:

- does its own checks of the files' format or columns when it is called, and raises
  one error that lists every problem it finds;
- pairs with a key function, such as
  :func:`~vivarium.dagger.frontend.root_inputs.location_key`, so that steps never spell
  the relative paths by hand.

A definition calls its input helpers inside ``pipeline()``, and may merge several
helpers' results with ``{**a, **b}``. When two merged mappings share a key, the later
one wins without an error, so give each helper's keys a distinct prefix. Input helpers
take different arguments for different conventions, so they share no base class; their
shared contract is the ``RootInputs`` return type and this convention.

This example merges a hand-written helper for files that every location shares with
the location inputs described below:

.. code-block:: python

    from pathlib import Path

    from vivarium.dagger.frontend import Pipeline, RootInputs, location_inputs

    HERE = Path(__file__).resolve().parent
    SHARED = Path("/path/to/shared/2026_11")
    SHARED_FILES = ["age_groups.csv"]
    LOCATIONS = ["ethiopia", "nigeria"]


    def shared_key(name: str) -> str:
        """Return the file key a step names to read the shared file ``name``."""
        return f"shared/{name}"


    def shared_inputs() -> RootInputs:
        """Map each shared reference file to its path, checking that all exist."""
        missing = [name for name in SHARED_FILES if not (SHARED / name).is_file()]
        if missing:
            raise FileNotFoundError(f"missing shared files under {SHARED}: {missing}")
        return {shared_key(name): SHARED / name for name in SHARED_FILES}


    def pipeline() -> Pipeline:
        """Add the steps that read the shared and location files."""
        root_inputs = {
            **location_inputs(HERE / "inputs", LOCATIONS, {"coverage.csv": ["coverage"]}),
            **shared_inputs(),
        }
        p = Pipeline(HERE / "output", run_name="example", root_inputs=root_inputs)
        ...
        return p

.. _location-inputs-helper:

location_inputs, one input helper
---------------------------------

:func:`~vivarium.dagger.frontend.root_inputs.location_inputs` finds and checks one set
of input files per location. When each location's files follow the convention below,
adding a location to the pipeline's list of locations is the only change the pipeline
needs.

Layout
~~~~~~

Each location's files go in a ``primary`` directory under an inputs root::

    <root>/<location>/primary/<file>

``<location>`` is a single directory name and ``<file>`` is a file name such as
``coverage.csv``. The pipeline definition names the inputs root. Put it on team
storage when the pipeline runs on the cluster, because the compute nodes read the
files; a local or temporary root works only for local runs.

Formats
~~~~~~~

Each file is a CSV file (comma-separated, UTF-8, with a header row) with the suffix
``.csv``, or a Parquet file with the suffix ``.parquet``. Each delivered file's name
must match the name the pipeline declares exactly. On a case-sensitive file system,
such as the cluster's, a file whose name differs only in case is reported as missing,
with the name that was found. Other formats, such as Excel workbooks, compressed CSV,
or HDF, are not accepted; convert them to CSV or Parquet first.

Declaring the files
~~~~~~~~~~~~~~~~~~~

A pipeline declares which files every location must supply, and the columns each file
must have, as a mapping from file name to column names. This mapping is the one place
that defines what each file must contain. Extra columns are allowed and column order
does not matter.

:func:`~vivarium.dagger.frontend.root_inputs.location_inputs` checks every declared
file for every location before anything runs. It reads only the column names (the CSV
header or the Parquet footer), so it is cheap even for large files. It does not check
values, data types, row counts, or whether a ``location_id`` column matches the
location's directory. A Parquet column stored as the index counts as present; a step
that reads the file with pandas gets that column as the index.

All problems are reported together, grouped by location, as one
:class:`~vivarium.dagger.frontend.root_inputs.LocationInputError` that names each
file's expected path. Because the check runs when the pipeline is defined, one location
whose data has not arrived means the pipeline cannot be defined, so nothing can be
built or previewed until its data is delivered or the location is removed from the
list.

Reading a file in a step
~~~~~~~~~~~~~~~~~~~~~~~~

Pass the mapping that :func:`~vivarium.dagger.frontend.root_inputs.location_inputs`
returns as the ``root_inputs`` of the pipeline's ``Pipeline``, or merge it into them.
Each key is the file's path relative to the inputs root, ``<location>/primary/<file>``,
and :func:`~vivarium.dagger.frontend.root_inputs.location_key` builds it. A step
reads one of these files by naming that key as an input, as for any root input.

Worked example
~~~~~~~~~~~~~~

This example is illustrative: the location name and the numbers are made up. The
inputs root holds one location's coverage file:

.. literalinclude:: ../../tests/frontend/location_inputs_example/inputs/example_location/primary/coverage.csv
   :caption: inputs/example_location/primary/coverage.csv

The definition declares the file, checks the location inputs, and adds one step per
location that reads it:

.. literalinclude:: ../../tests/frontend/location_inputs_example/pipeline.py
   :language: python
   :caption: pipeline.py

The library's tests build this example, so the page and the code cannot drift apart.
