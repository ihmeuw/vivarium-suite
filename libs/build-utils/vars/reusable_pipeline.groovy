def call(Map config = [:]){
  /* This is the funtion called from the repo
  Example: fhs_standard_pipeline(job_name: JOB_NAME)
  JOB_NAME is a reserved Jenkins var
  -------------
  Configuration options:
  scheduled_branches: The branch names for which to run scheduled nightly builds.
  stagger_scheduled_builds: Whether to stagger the scheduled builds.
  test_types: The tests to run. Must be subset (inclusive) of ['unit', 'integration', 'e2e', 'all']
  weekly_tests: Whether the package has tests marked `weekly`. Sunday cron builds and builds
            with RUN_WEEKLY set pass RUNWEEKLY=1 and get a "[weekly]" label in the build name.
  requires_slurm: Whether the child tasks require the slurm scheduler. Can also be set to "weekly" to
            use slurm only for builds that run weekly tests. Setting to "weekly" also sets weekly_tests.
  deployable: Whether the package can be deployed by Jenkins. Only builds Jenkins
            starts for a new commit deploy; anything else needs FORCE_DEPLOY.
  skip_doc_build: Only skips the doc build.
  run_mypy: DEPRECATED and ignored. mypy now runs automatically whenever a
            py.typed marker exists under the package's src/ (matching `make check`
            and GH Actions).
  env_reqs: The pyproject.toml extras to install with `make install` (e.g. "ci_jenkins").
            Empty/omitted leaves base.mk's default ("dev"), which is correct for standalone repos.
            With uv_lock, these are the extras to sync, separated by commas, and
            empty/omitted means no extras.
  uv_lock: Build the environment from the repo's committed uv.lock with `uv sync`,
            instead of a conda environment and `make install`. Standalone repos only,
            and not with deployable.
  uv_git_credentials_id: With uv_lock, the Jenkins credential that uv uses to clone
            git sources in uv.lock from private repos. Empty/omitted clones without
            credentials, which works only for public repos.
  github_credentials_id: Jenkins credential ID to use during the deploy stage when pushing
            the release tag (only consulted when `deployable: true`). Empty/omitted falls
            back to the credential configured on the Multibranch Pipeline's branch source.
  */

  // Handle config arguments
  def supportedArgs = [
    'scheduled_branches',
    'stagger_scheduled_builds',
    'test_types',
    'requires_slurm',
    'weekly_tests',
    'deployable',
    'skip_doc_build',
    'run_mypy',
    'env_reqs',
    'uv_lock',
    'uv_git_credentials_id',
    'github_credentials_id'
  ]

  def scheduled_branches = config.scheduled_branches ?: []
  def stagger_scheduled_builds = config.stagger_scheduled_builds ?: false
  def test_types = config.test_types ?: ['all']
  def requires_slurm = config.requires_slurm ?: false
  def weekly_tests = (config?.weekly_tests == true) || (requires_slurm == "weekly")
  def is_deployable = (config?.deployable == true)
  def skip_doc_build = (config?.skip_doc_build == true)
  // DEPRECATED: run_mypy no longer controls anything. mypy runs in checkFormatting
  // whenever a py.typed marker exists under src/. Accepted for backward compatibility.
  if (config.run_mypy != null) {
    echo "WARNING: 'run_mypy' is deprecated and ignored; mypy now runs automatically " +
         "when a py.typed marker exists under src/."
  }
  // Empty string leaves base.mk's default ("dev") in effect. installPackage in
  // build_stages.groovy only sets ENV_REQS=... when this is non-empty.
  def env_reqs = config.env_reqs ?: ""
  // Opt-in, so a repo that commits a uv.lock for local use does not switch by accident.
  def use_uv_lock = (config?.uv_lock == true)
  def uv_git_credentials_id = config.uv_git_credentials_id ?: ""
  // Optional override for the GitHub credential used for git push during deploy.
  // When empty, deployPackage() falls back to the credential from scm (branch source).
  def github_credentials_id = config.github_credentials_id ?: ""

  // task_node, conda_env_dir, and run_weekly are resolved in the Initialization
  // stage after user parameters are available (needed for RUN_WEEKLY override).
  def task_node
  def conda_env_dir
  def uv_python_dir
  def run_weekly
  conda_env_name_base = "${env.JOB_NAME}-${BUILD_NUMBER}"

  echo "Configuration constants:"
  echo "  scheduled_branches: ${scheduled_branches}"
  echo "  stagger_scheduled_builds: ${stagger_scheduled_builds}"
  echo "  test_types: ${test_types}"
  echo "  requires_slurm: ${requires_slurm}"
  echo "  weekly_tests: ${weekly_tests}"
  echo "  is_deployable: ${is_deployable}"
  echo "  skip_doc_build: ${skip_doc_build}"
  echo "  env_reqs: ${env_reqs}"
  echo "  uv_lock: ${use_uv_lock}"

  if (stagger_scheduled_builds && scheduled_branches.size() > 1) {
    startHour = 20
    endHour = 23
    minutesRange = (endHour - startHour + 1) * 60
    // distribute branches evenly across the range
    int startMinute = scheduled_branches.indexOf(BRANCH_NAME) * (minutesRange / scheduled_branches.size())
    int cronHour = startHour + (startMinute / 60) as int
    int cronMinute = startMinute % 60 as int
    cron_schedule = scheduled_branches.contains(BRANCH_NAME) ? "${cronMinute} ${cronHour} * * *" : ''
  } else {
    cron_schedule = scheduled_branches.contains(BRANCH_NAME) ? "H H(20-23) * * *" : ''
  }

  pipeline {
    environment {
        IS_CRON = "${currentBuild.buildCauses.toString().contains('TimerTrigger')}"
        // Jenkins starting a build because it found a new commit. Today that is the
        // branch scan; BranchEventCause covers a webhook if one is ever wired up.
        IS_NEW_COMMIT = "${currentBuild.buildCauses.toString().contains('BranchIndexingCause') || currentBuild.buildCauses.toString().contains('BranchEventCause')}"
        CRON_SCHEDULE = "${cron_schedule}"
        // defaults for conda and pip are a local scratch directory /svc-simsci for improved speed.
        // In the past, we used the cluster filesystem which is much slower.
        shared_path="/svc-simsci"
        // Get the branch being built and strip everything but the text after the last "/"
        BRANCH = sh(script: "echo ${GIT_BRANCH} | rev | cut -d '/' -f1 | rev", returnStdout: true).trim()
        TIMESTAMP = sh(script: 'date', returnStdout: true)
        // Specify the path to the .condarc file via environment variable.
        // This file configures the shared conda package cache.
        CONDARC = "${shared_path}/miniconda3/.condarc"
        CONDA_BIN_PATH = "${shared_path}/miniconda3/bin"
        // Set the Pip cache.
        XDG_CACHE_HOME = "${shared_path}/pip-cache"
        // Jenkins commands run in separate processes, so need to activate the environment every
        // time we run pip, poetry, etc.
        ACTIVATE_BASE = "source ${CONDA_BIN_PATH}/activate &> /dev/null"
    }

    // This agent runs as svc-simsci on node simsci-ci-coordinator-01.
    // It has access to standard IHME filesystems and singularity
    agent { label "coordinator" }

    options {
      // Keep 100 old builds.
      buildDiscarder logRotator(numToKeepStr: "100")
    }

    parameters {
      booleanParam(
        name: "FORCE_DEPLOY",
        defaultValue: false,
        description: "Whether to deploy from this build of the default branch. Only builds Jenkins starts for a new commit deploy on their own; set this to release from a build you started by hand."
      )
      booleanParam(
        name: "RUN_SLOW",
        defaultValue: false,
        description: "Whether to run slow tests as part of pytest suite."
      )
      booleanParam(
        name: "RUN_WEEKLY",
        defaultValue: false,
        description: "Whether to run weekly tests and label the build [weekly]. Has no effect unless the Jenkinsfile sets weekly_tests (or requires_slurm: \"weekly\")."
      )
      booleanParam(
        name: "FORCE_FULL_BUILD",
        defaultValue: false,
        description: "Force a complete build regardless of change type (overrides doc-only and changelog-only skip logic)."
      )
      booleanParam(
        name: "FAIL_FAST",
        defaultValue: false,
        description: "Stop at the first failing check and abort the other Python versions. By default every check runs on every Python version so the build reports all failures."
      )
      string(
        name: "SLACK_TO",
        defaultValue: "",
        description: "The Slack channel to send messages to."
      )
      booleanParam(
        name: "DEBUG",
        defaultValue: false,
        description: "Used as needed for debugging purposes."
      )
    }

    triggers {
      cron(cron_schedule)
    }

    stages {
      stage("Configuration validation") {
        steps {
          script {
            // Validate the configuration arguments
            def unsupportedArgs = config.keySet() - supportedArgs
            if (unsupportedArgs) {
              error(
                "Unsupported configuration arguments: ${unsupportedArgs.join(', ')}. " +
                "Supported arguments are: ${supportedArgs.join(', ')}"
              )
            }
            // Validate test_types
            if (!test_types.every { ['all', 'e2e', 'unit', 'integration'].contains(it) }) {
              error("test_types must be a subset of ['all', 'e2e', 'unit', 'integration']")
            }
            if (use_uv_lock && is_deployable) {
              error("uv_lock cannot be combined with deployable: Jenkins does not deploy from a uv repo.")
            }
            if (uv_git_credentials_id && !use_uv_lock) {
              error("uv_git_credentials_id only applies with uv_lock: true.")
            }
            if (requires_slurm == "weekly" && config.containsKey('weekly_tests') && config.weekly_tests != true) {
              error("requires_slurm: \"weekly\" needs weekly tests, so weekly_tests cannot be false.")
            }
          }
        }
      }
      stage("Initialization") {
        steps {
          script {
            // Use the name of the branch in the build name
            currentBuild.displayName = "#${BUILD_NUMBER} ${GIT_BRANCH}"
            env.PACKAGE_SUBDIR = get_package_subdir()
            if (use_uv_lock && env.PACKAGE_SUBDIR) {
              error("uv_lock is for standalone repos only, not monorepo libs (${env.PACKAGE_SUBDIR}).")
            }
            python_versions = get_python_versions(WORKSPACE, GIT_URL, env.PACKAGE_SUBDIR)
            // Derive the deploy/docs version as the last entry in the list
            PYTHON_DEPLOY_VERSION = python_versions[-1]
            echo "Python deploy version (inferred): ${PYTHON_DEPLOY_VERSION}"

            // Weekly tests run on Sunday cron builds, or when the user sets RUN_WEEKLY.
            // Only packages that declare weekly_tests get RUNWEEKLY=1 and the "[weekly]" label.
            run_weekly = false
            def weekly_reason = ""
            if (weekly_tests) {
              def dayOfWeek = new Date().format('EEEE')
              if (params.RUN_WEEKLY) {
                run_weekly = true
                weekly_reason = "RUN_WEEKLY parameter"
              } else if (env.IS_CRON.toBoolean() && dayOfWeek == 'Sunday') {
                run_weekly = true
                weekly_reason = "Sunday cron"
              }
            }
            if (run_weekly) {
              currentBuild.displayName += " [weekly]"
              currentBuild.description = "Weekly tests requested (${weekly_reason})"
            }
            def use_slurm = (requires_slurm == "weekly") ? run_weekly : (requires_slurm ? true : false)
            task_node = use_slurm ? 'slurm' : 'matrix-tasks'
            conda_env_dir = use_slurm ? "/mnt/team/simulation_science/priv/engineering/jenkins/envs" : "/svc-simsci/envs"
            // A uv venv links to its Python, so Slurm builds need the Python on the shared
            // filesystem too. Both directories are outside Jenkinsfile.cleanup's sweep, so
            // each Python is installed once, not once per build.
            uv_python_dir = use_slurm ? "/mnt/team/simulation_science/priv/engineering/jenkins/uv-python" : "/svc-simsci/uv-python"
            echo "Resolved task_node: ${task_node}, conda_env_dir: ${conda_env_dir}"
          }
        }
      }
      stage("Python Versions") {
        steps {
          script {
            
            def buildStages = build_stages()
            
            def parallelPythonVersions = [:]
            
            python_versions.each { pythonVersion ->
              parallelPythonVersions["Python ${pythonVersion}"] = {
                node(task_node) {
                  // On the uv path the venv takes the conda environment's place, so the
                  // CONDA_ENV_* names still hold its path.
                  def env_path = "${conda_env_dir}/${conda_env_name_base}-${pythonVersion}"
                  def envVars = [
                    CONDA_ENV_NAME: "${conda_env_name_base}-${pythonVersion}",
                    CONDA_ENV_PATH: env_path,
                    PYTHON_VERSION: pythonVersion,
                    USE_UV_LOCK: use_uv_lock.toString(),
                    UV_PYTHON_INSTALL_DIR: uv_python_dir,
                    ACTIVATE: use_uv_lock ?
                      "source ${env_path}/bin/activate &> /dev/null" :
                      "source /svc-simsci/miniconda3/bin/activate ${env_path} &> /dev/null",
                  ]
                  
                  withEnv(envVars.collect { k, v -> "${k}=${v}" }) {
                    try {
                      // The branch source fetches branches but not tags. setuptools_scm needs the
                      // <dist>-v<X.Y.Z> tags to compute the version of the lib under test, and without
                      // them it falls back to 0.0.1.dev, which fails any install that requires a
                      // minimum version of that lib (e.g. engine's test extra, via vivarium-interactive).
                      // Check out with the job's own remotes, credentials and extensions, so the tags
                      // come from the authenticated fetch and private repos work too. The branch source
                      // adds GitSCMSourceDefaults, which clones without tags, and a clone option cannot
                      // turn tags back on for a fresh clone, so drop it and honor the refspec here instead.
                      // The extension list is built inline because a local variable holding it cannot be
                      // serialized when the pipeline saves its state.
                      checkout([
                        $class: 'GitSCM',
                        branches: scm.branches,
                        userRemoteConfigs: scm.userRemoteConfigs,
                        extensions: scm.extensions.findAll {
                          it.class.name != 'jenkins.plugins.git.GitSCMSourceDefaults'
                        } + [[$class: 'CloneOption', noTags: false, shallow: false, reference: '', honorRefspec: true]],
                      ])
                      buildStages.loadSharedFiles()
                      
                      // Evaluate skip conditions after checkout (GIT_PREVIOUS_COMMIT is now available)
                      def previousBuildPassed = previous_build_passed()
                      def isDocOnlyChange = git_utils.isChangeOnlyMatching('^docs/', 'docs-only')
                      def isChangelogOnlyChange = git_utils.isChangeOnlyMatching('^CHANGELOG', 'changelog-only')
                      def isCron = env.IS_CRON.toBoolean()
                      def forceFullBuild = params.FORCE_FULL_BUILD
                      
                      echo "Skip evaluation: previousBuildPassed=${previousBuildPassed}, isDocOnlyChange=${isDocOnlyChange}, isChangelogOnlyChange=${isChangelogOnlyChange}, isCron=${isCron}, forceFullBuild=${forceFullBuild}"
                      
                      // Determine if we should skip the full build
                      def canSkipFullBuild = previousBuildPassed && !isCron && !forceFullBuild && !run_weekly
                      def skipForChangelogOnly = canSkipFullBuild && isChangelogOnlyChange
                      def skipForDocOnly = canSkipFullBuild && isDocOnlyChange
                      
                      // Prepare skip evaluation info for debug output
                      def skipEval = [
                        previousBuildPassed: previousBuildPassed,
                        isDocOnlyChange: isDocOnlyChange,
                        isChangelogOnlyChange: isChangelogOnlyChange,
                        canSkipFullBuild: canSkipFullBuild,
                        skipForDocOnly: skipForDocOnly,
                        skipForChangelogOnly: skipForChangelogOnly
                      ]

                      // Use pip cache only for push/PR
                      boolean useCache = !env.IS_CRON.toBoolean() && env.BRANCH != "main"

                      if (skipForChangelogOnly) {
                        echo "This is a changelog-only change since last build and previous build passed. Skipping entire build."
                        // No build steps needed - just let it fall through to cleanup
                        currentBuild.result = 'SUCCESS'  // Mark build as successful since we're intentionally skipping
                      } else if (skipForDocOnly) {
                        echo "This is a doc-only change since last build and previous build passed. Skipping everything except doc build and doc tests."
                        buildStages.runDebugInfo(skipEval)
                        if (use_uv_lock) {
                          buildStages.syncEnvironment(env_reqs, useCache, uv_git_credentials_id)
                        } else {
                          buildStages.buildEnvironment()
                          buildStages.installPackage("docs", useCache)
                        }
                        buildStages.buildDocs()
                        buildStages.testDocs()
                      } else {
                        buildStages.runDebugInfo(skipEval)
                        if (use_uv_lock) {
                          buildStages.syncEnvironment(env_reqs, useCache, uv_git_credentials_id)
                        } else {
                          buildStages.buildEnvironment()
                          buildStages.installPackage(env_reqs, useCache)
                        }
                        buildStages.checkFormatting()
                        // Transform test type inputs to actual make test target names
                        tests = test_types.collect { "test-${it}" }
                        buildStages.runTests(tests, run_weekly)

                        if (PYTHON_VERSION == PYTHON_DEPLOY_VERSION) {
                          if (!skip_doc_build) {
                            buildStages.buildDocs()
                            buildStages.testDocs()
                          }
                          
                          stage("Build and Deploy - Python ${pythonVersion}") {
                            def canDeploy = env.IS_NEW_COMMIT.toBoolean() || params.FORCE_DEPLOY
                            if (is_deployable &&
                              (env.BRANCH == "main") &&
                              has_deployable_change()) {
                              if (!canDeploy) {
                                echo "Skipping deploy: Jenkins did not start this build for a " +
                                     "new commit. Set FORCE_DEPLOY to release from it."
                              } else if (currentBuild.currentResult != 'SUCCESS') {
                                // Checks no longer stop the build when they fail, so the
                                // build can reach this point after a failed check.
                                echo "Skipping deploy: a check failed earlier in this build " +
                                     "(build result: ${currentBuild.currentResult})."
                              } else {
                                if (!has_changelog_update()) {
                                  error "Deploy failed: Changelog does not contain a proper version update."
                                }
                                def deployOpts = github_credentials_id ? [gitCredentialsId: github_credentials_id] : [:]
                                buildStages.deployPackage(deployOpts)

                                if (!skip_doc_build) {
                                  buildStages.deployDocs()
                                }
                              }
                            }
                          }
                        }
                      }
                    } finally {
                      // Cleanup
                      script {
                        if (params.DEBUG) {
                          echo 'Debug is enabled - keeping workspace but cleaning @tmp directory.'
                          buildStages.cleanupDebug()
                        } else {
                          buildStages.cleanup()
                        }
                      }
                    }
                  }
                }
              }
            }

            parallelPythonVersions.failFast = (params.FAIL_FAST == true)
            parallel parallelPythonVersions
          }
        }
      }
    }

    post {
      always {
        // Generate a message to send to Slack.
        script {
          // Run git command to get the author of the last commit
          developerID = sh(
            script: "git log -1 --pretty=format:'%an'",
            returnStdout: true
          ).trim()
          if (params.SLACK_TO) {
            channelName = params.SLACK_TO
            slackID = "channel"
          } else if (env.BRANCH == "main" || scheduled_branches.contains(env.BRANCH_NAME)) {
            channelName = "simsci-ci-status"
            slackID = "channel"
          } else {
            channelName = "simsci-ci-status-test"
            slackID = github_slack_mapper(github_author: developerID)
          }
          echo "slackID to tag in slack message: ${slackID}"
          slackMessage = """
            Job: *${env.JOB_NAME}*
            Build number: #${env.BUILD_NUMBER}
            Build status: *${currentBuild.result}*
            Author: @${slackID}
            Build details: <${env.BUILD_URL}/console|See in web console>
            """.stripIndent()
          if (params.DEBUG) {
            slackMessage += """
              
              Debug was enabled - MANUALLY CLEAN UP WHEN FINISHED.
              1. Env path: ${conda_env_dir}/${conda_env_name_base} (on ${task_node} node)
              2. Workspace: ${env.WORKSPACE}
              """.stripIndent()
          }
        }
      }
      failure {
        echo "This build triggered by ${developerID} failed on ${GIT_BRANCH}. Sending a failure message to Slack."
        slackSend channel: "#${channelName}",
                  message: slackMessage,
                  teamDomain: "ihme",
                  tokenCredentialId: "slack"
      }
      success {
        script {
          if (params.DEBUG) {
            echo 'Debug is enabled. Sending a success message to Slack.'
            slackSend channel: "#${channelName}",
                      message: slackMessage,
                      teamDomain: "ihme",
                      tokenCredentialId: "slack"
          } else {
            echo 'Debug is not enabled. No success message will be sent to Slack.'
          }
        }
      }
      cleanup { // cleanup for outer workspace
        // NOTE: We always clean up this outer workspace regardless of DEBUG
        // deleteDirs: true ensures both WORKSPACE and WORKSPACE@tmp are cleaned
        // disableDeferredWipeout: true forces immediate deletion instead of background cleanup
        // Console logs are preserved in Jenkins home, not workspace
        cleanWs(deleteDirs: true, disableDeferredWipeout: true)
      }
    }  // End of post
  }  // End of pipeline
}
