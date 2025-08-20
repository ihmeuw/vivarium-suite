def call(Map config = [:]){
  /* This is the funtion called from the repo
  Example: fhs_standard_pipeline(job_name: JOB_NAME)
  JOB_NAME is a reserved Jenkins var
  -------------
  Configuration options:
  scheduled_branches: The branch names for which to run scheduled nightly builds.
  stagger_scheduled_builds: Whether to stagger the scheduled builds.
  test_types: The tests to run. Must be subset (inclusive) of ['unit', 'integration', 'e2e', 'all']
  requires_slurm: Whether the child tasks require the slurm scheduler.
  deployable: Whether the package can be deployed by Jenkins.
  skip_doc_build: Only skips the doc build.
  upstream_repos: A list of repos to check for upstream changes.
  run_mypy: Whether to run mypy on the package
  */
  
  // Handle config arguments
  def supportedArgs = [
    'scheduled_branches',
    'stagger_scheduled_builds', 
    'test_types',
    'requires_slurm',
    'deployable',
    'skip_doc_build',
    'upstream_repos',
    'run_mypy'
  ]
  
  def scheduled_branches = config.scheduled_branches ?: [] 
  def stagger_scheduled_builds = config.stagger_scheduled_builds ?: false
  def test_types = config.test_types ?: ['all']
  def task_node = config.requires_slurm ? 'slurm' : 'matrix-tasks'
  def is_deployable = (config?.deployable == true)
  def skip_doc_build = (config?.skip_doc_build == true)
  def upstream_repos = config.upstream_repos ?: []
  def run_mypy = (config.run_mypy != null) ? config.run_mypy : true

  echo "Configuration constants:"
  echo "  scheduled_branches: ${scheduled_branches}"
  echo "  stagger_scheduled_builds: ${stagger_scheduled_builds}"
  echo "  test_types: ${test_types}"
  echo "  task_node: ${task_node}"
  echo "  is_deployable: ${is_deployable}"
  echo "  skip_doc_build: ${skip_doc_build}"
  echo "  upstream_repos: ${upstream_repos}"
  echo "  run_mypy: ${run_mypy}"

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

  PYTHON_DEPLOY_VERSION = "3.11"
  
  conda_env_name_base = "${env.JOB_NAME}-${BUILD_NUMBER}"
  conda_env_dir = "/mnt/team/simulation_science/priv/engineering/jenkins/envs"

  pipeline {
    // This agent runs as svc-simsci on node simsci-ci-coordinator-01.
    // It has access to standard IHME filesystems and singularity
    environment {
        IS_CRON = "${currentBuild.buildCauses.toString().contains('TimerTrigger')}"
        CRON_SCHEDULE = "${cron_schedule}"
        // defaults for conda and pip are a local directory /svc-simsci for improved speed.
        // In the past, we used /ihme/code/* on the NFS (which is slower)
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
        IS_DOC_ONLY_CHANGE = "${is_doc_only_change()}"
    }

    agent { label "coordinator" }

    options {
      // Keep 100 old builds.
      buildDiscarder logRotator(numToKeepStr: "100")

      // Fail immediately if any part of a parallel stage fails
      parallelsAlwaysFailFast()
    }

    parameters {
      booleanParam(
        name: "SKIP_DEPLOY",
        defaultValue: false,
        description: "Whether to skip deploying on a run of the default branch."
      )
      booleanParam(
        name: "RUN_SLOW",
        defaultValue: false,
        description: "Whether to run slow tests as part of pytest suite."
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
          }
        }
      }
      stage("Initialization") {
        steps {
          script {
            // Use the name of the branch in the build name
            currentBuild.displayName = "#${BUILD_NUMBER} ${GIT_BRANCH}"
            python_versions = get_python_versions(WORKSPACE, GIT_URL)
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
                  def envVars = [
                    CONDA_ENV_NAME: "${conda_env_name_base}-${pythonVersion}",
                    CONDA_ENV_PATH: "${conda_env_dir}/${conda_env_name_base}-${pythonVersion}",
                    PYTHON_VERSION: pythonVersion,
                    ACTIVATE: "source /svc-simsci/miniconda3/bin/activate ${conda_env_dir}/${conda_env_name_base}-${pythonVersion} &> /dev/null",
                  ]
                  
                  withEnv(envVars.collect { k, v -> "${k}=${v}" }) {
                    try {
                      checkout scm
                      load_shared_files()
                      buildStages.runDebugInfo()
                      buildStages.buildEnvironment()
                      if (IS_DOC_ONLY_CHANGE.toBoolean() == true) {
                        echo "This is a doc-only change. Skipping everything except doc build and doc tests."
                        buildStages.installPackage("docs")
                        buildStages.buildDocs()
                        buildStages.testDocs()
                      } else {
                        buildStages.installPackage()
                        buildStages.installDependencies(upstream_repos)
                        buildStages.checkFormatting(run_mypy)
                        // Transform test type inputs to actual make test target names
                        tests = test_types.collect { "test-${it}" }
                        buildStages.runTests(tests)

                        if (PYTHON_VERSION == PYTHON_DEPLOY_VERSION) {
                          if (!skip_doc_build) {
                            buildStages.buildDocs()
                            buildStages.testDocs()
                          }
                          
                          stage("Build and Deploy - Python ${pythonVersion}") {
                            if (is_deployable &&
                              !env.IS_CRON.toBoolean() &&
                              !params.SKIP_DEPLOY &&
                              (env.BRANCH == "main") &&
                              has_deployable_change()) {
                              if (!has_changelog_update()) {
                                error "Deploy failed: Changelog does not contain a proper version update."
                              }
                              buildStages.deployPackage()

                              if (!skip_doc_build) {
                                buildStages.deployDocs()
                              }
                            }
                          }
                        }
                      }
                    } finally {
                      // Cleanup
                      script {
                        if (params.DEBUG) {
                          echo 'Debug was enabled - MANUALLY CLEAN UP WHEN FINISHED.'
                        } else {
                          buildStages.cleanup()
                        }
                      }
                    }
                  }
                }
              }
            }

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
          } else if (env.BRANCH == "main") {
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
              1. Env path: ${conda_env_dir}/${conda_env_name_base}
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
        cleanWs()
        // manually remove @tmp dirs
        dir("${WORKSPACE}@tmp"){
          deleteDir()
        }
      }
    }  // End of post
  }  // End of pipeline
}
