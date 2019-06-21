**1.0.14 - 06/20/19**

 - Bugfix in job enqueuing.

**1.0.12 - 06/20/19**

 - Bugfix in worker invocation

**1.0.11 - 06/19/19**

 - Namespace bugfix.

**1.0.10 - 06/18/19**

 - Added option to not batch results.
 - Updated yaml api usage.
 - Bugfix in dtypes when writing results.
 - Allow the usage of branch files without parameter variations.
 - Don't use ``ResultsWriter`` directly.
 - Switch to consistent pathlib usage.
 - Enable restart when no parameter variations present.
 - Setup log rotations.
 - Forward vivarium logs to worker logs.
 - New ``vparse`` command for parsing worker logs.

**1.0.9 - 04/22/19**

 - Bugfix in restart with string columns in outputs.
 - Extract common cli options.
 - Add verbosity levels to logging.
 - Add command to add draws/seeds to previous runs.

**1.0.8 - 04/16/19**

 - Switch to loguru for logging and cleanup usage.
 - Log cluster and node information from the workers.
 - Add serialized logs.
 - Client side sharding of redis instances.
 - More robustness in failure handling.
 - Tutorial documentation for yaml syntax.
 - Tutorial documentation for branches files.
 - Tutorial documentation for psimulate.
 - Readthedocs integration.
 - Extraction of shared CLI options

**1.0.7 - 04/02/19**

 - Be defensive about retrieving jobs.

**1.0.6 - 03/29/19**

 - Bugfix in tests.

**1.0.5 - 03/29/19**

 - Migrate to github.

**1.0.4 - 03/28/19**

 - Add debugger to cli.

**1.0.3 - 03/20/19**

 - Reduce requirements for simulation jobs.

**1.0.2 - 03/19/19**

 - Hack around hard to reproduce drmaa error.

**1.0.1 - 03/15/19**

 - Add additional project options.

**1.0.0 - 02/22/19**

 - Initial release.
