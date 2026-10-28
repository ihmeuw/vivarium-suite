=====================
Primary-data handoff
=====================

A pipeline often starts from data that an analyst delivers for each location, such as
a coverage table for a new country. This page describes where those files go, which
formats are accepted, and how a pipeline declares and reads them. When a location's
files follow this convention, adding the location to the pipeline's list of locations
is the only change the pipeline needs.

Layout
------

Each location's files go in a ``primary`` directory under a handoff root::

    <root>/<location>/primary/<file>

``<location>`` is a single directory name and ``<file>`` is a file name such as
``coverage.csv``. The pipeline definition names the handoff root. Put it on team
storage when the pipeline runs on the cluster, because the compute nodes read the
files; a local or temporary root works only for local runs.

Formats
-------

A handoff file is a CSV file (comma-separated, UTF-8, with a header row) with the
suffix ``.csv``, or a Parquet file with the suffix ``.parquet``. Each delivered file's
name must match the name the pipeline declares exactly. On a case-sensitive file
system, such as the cluster's, a file whose name differs only in case is reported as
missing, with the name that was found. Other formats, such as
Excel workbooks, compressed CSV, or HDF, are not accepted; convert them to CSV or
Parquet first.

Declaring the files
-------------------

A pipeline declares which files every location must supply, and the columns each file
must have, as a mapping from file name to column names. This mapping is the one place
that defines what each file must contain. Extra columns are allowed and column order
does not matter.

:func:`~vivarium.workflow.frontend.handoff.handoff_inputs` checks every declared file
for every location before anything runs. It reads only the column names (the CSV
header or the Parquet footer), so it is cheap even for large files. It does not check
values, data types, row counts, or whether a ``location_id`` column matches the
location's directory. A Parquet column stored as the index counts as present; a step
that reads the file with pandas gets that column as the index.

All problems are reported together, grouped by location, as one
:class:`~vivarium.workflow.frontend.handoff.HandoffError` that names each file's
expected path. Because the check runs when the pipeline is defined, one location whose
data has not arrived means the pipeline cannot be defined, so nothing can be built or
previewed until its data is delivered or the location is removed from the list.

Reading a file in a step
------------------------

Pass the mapping that :func:`~vivarium.workflow.frontend.handoff.handoff_inputs`
returns as the ``root_inputs`` of an
:class:`~vivarium.workflow.backend.wiring.ArtifactRegistry`. Each key is the file's
path relative to the handoff root, ``<location>/primary/<file>``, and
:func:`~vivarium.workflow.frontend.handoff.handoff_key` builds it. A step reads a
handoff file by naming that key as an input, and the step's function receives the
file's path. A step cannot also produce a handoff key: the registry refuses an output
whose key is a declared root input.

Worked example
--------------

This example is illustrative: the location name and the numbers are made up. The
handoff root holds one location's coverage file:

.. literalinclude:: ../../tests/frontend/handoff_example/handoff/example_location/primary/coverage.csv
   :caption: handoff/example_location/primary/coverage.csv

The definition declares the file, validates the handoff, and compiles one step per
location that reads it:

.. literalinclude:: ../../tests/frontend/handoff_example/pipeline.py
   :language: python
   :caption: pipeline.py

The library's tests build this example, so the page and the code cannot drift apart.
