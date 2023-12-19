.. toctree::
   :maxdepth: 2
   :caption: Contents:

Logging
============

Sometimes, even with perfect code, things can go wrong at sufficient scale.
When they do, it's useful to look to the logs to see what happened.  ``psimulate``
logs to the results directory, in a subdirectory called ``logs``. Inside that directory,
there will be a directory for each simulation run or restart. If neither
``psimulate restart`` nor ``psimulate expand`` was
ever used for the run, there will be only one directory for the run.

Top-level logs
----------------
At the top-level of the directory, there will be text and JSON-formatted main log files.
These are the log files for the runner process. There will also be a log file for each
Redis database process, which will be named ``redis.p<port>.log``. Per-worker logs are
in ``cluster_logs`` and ``worker_logs`` directories, described below.

Cluster logs
-------------
The ``cluster_logs`` directory contains logs from the the array job processes. Each worker job
has its own file. The contents of these are similar to what you will find in the ``worker_logs``
directory, but a superset. The logs in the ``cluster_logs`` directory contain information about Redis
heartbeats and other cluster-related information.

Worker logs
-------------
The ``worker_logs`` directory contains logs from the the worker processes as they relate
running simulations. Additionally this directory contains performance logs that
are described in the next section.

Performance logs
-----------------
As part of the VIPIN (VIvarium Performance INformation) feature, ``psimulate`` gathers
per-worker performance information. This information is summarized at the end of the parallel
runs and stored in the ``worker_logs`` directory as ``log_summary.csv``. This file
contains metadata identifying the run and the worker host, execution timing information, and CPU,
disk, and network performance counters. The intent of this logging is to allow users to understand the
performance characteristics of their simulations and in the event of suspicious performance,
to be able to correlate outlier performance characteristics to cluster and hardware events.
