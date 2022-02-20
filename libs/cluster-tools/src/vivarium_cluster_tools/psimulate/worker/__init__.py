"""
====================
psimulate RQ Workers
====================

Launching RQ workers is done by specifying Python import paths to worker implementations.
The actual worker classes and functions are hidden from view here as they should not be
referenced directly in the main `psimulate` process.

There are three pieces to an RQ worker.

#. The Worker Class. The worker class and is a normal Python class that inherits from
   `rq.Worker` (this default implementation is fully functional on its own). The worker
   class governs interaction with the Redis DB where the jobs and results are stored,
   the retry behavior on DB connection errors, and the forking behavior when it runs
   a job, and a number of other things. The `ResilientWorker` defined in the `core` module
   primarily makes some small adjustments to the logging and retry behavior and squashes
   some undesirable forking behavior on clusters that can orphan child processes.
#. The Exception handler. The exception handler is a plain python function that determines
   what happens when an actual job fails due to something other than a Redis DB connection
   issue. These failures usually occur when there is a code error, a data error, or
   an inability to access some resource within the job itself (filesystem, database, etc.).
#. The Work Horse.  The work horse is also a plain python function. It is the actual
   execution logic of a job (a job is a blob of parameters retrieved by the worker class
   and passed in to the work horse). The function takes a single argument which is
   deserialized off the job queue in the connected Redis database. The body of the function
   can really do anything at that point. It can then return a value which will be serialized
   (using pickle, I'm pretty sure) and stored back in the results section of the connected
   Redis DB.

The `build_launch_script` function provided by this package produces a shell file
used to spin up our (slightly customized) general purpose worker class with an exception
handler that will retry jobs a couple of times.

"""
from vivarium_cluster_tools.psimulate import COMMANDS
from vivarium_cluster_tools.psimulate.worker.core import build_launch_script
from vivarium_cluster_tools.psimulate.worker.load_test_work_horse import (
    LOAD_TEST_WORK_HORSE_IMPORT_PATH,
)

# Work horses are specific to a kind of job.
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import (
    VIVARIUM_WORK_HORSE_IMPORT_PATH,
)

# All work horses available to psimulate
WORK_HORSE_PATHS = {
    COMMANDS.run: VIVARIUM_WORK_HORSE_IMPORT_PATH,
    COMMANDS.restart: VIVARIUM_WORK_HORSE_IMPORT_PATH,
    COMMANDS.expand: VIVARIUM_WORK_HORSE_IMPORT_PATH,
    COMMANDS.load_test: LOAD_TEST_WORK_HORSE_IMPORT_PATH,
}

# Clean up the namespace
del VIVARIUM_WORK_HORSE_IMPORT_PATH
del LOAD_TEST_WORK_HORSE_IMPORT_PATH
del COMMANDS
