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
  task_node = config.requires_slurm ? 'slurm' : 'matrix-tasks'

  scheduled_branches = config.scheduled_branches ?: [] 
  stagger_scheduled_builds = config.stagger_scheduled_builds ?: false

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

  test_types = config.test_types ?: ['all']
  // raise an error if test_types is not a subset of  ['e2e', 'unit', 'integration']
  if (!test_types.every { ['all', 'e2e', 'unit', 'integration'].contains(it) }) {
    throw new IllegalArgumentException("test_types must be a subset of ['all', 'e2e', 'unit', 'integration']")
  }
  
  // Transform test type inputs to actual make test target names
  test_types = test_types.collect { "test-${it}" }
  
  conda_env_name_base = "${env.JOB_NAME}-${BUILD_NUMBER}"
  conda_env_dir = "/mnt/team/simulation_science/priv/engineering/jenkins/envs"

  // Define the upstream repos to check for changes
  upstream_repos = config.upstream_repos ?: []
  // Define whether to run mypy
  run_mypy = config.run_mypy != null ? config.run_mypy : true

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
                        buildStages.runTests(test_types)

                        if (PYTHON_VERSION == PYTHON_DEPLOY_VERSION) {
                          if (config?.skip_doc_build != true) {
                            buildStages.buildDocs()
                            buildStages.testDocs()
                          }
                          
                          stage("Build and Deploy - Python ${pythonVersion}") {
                            if ((config?.deployable == true) &&
                              !env.IS_CRON.toBoolean() &&
                              !params.SKIP_DEPLOY &&
                              (env.BRANCH == "main") &&
                              has_deployable_change()) {
                              if (!has_changelog_update()) {
                                error "Deploy failed: Changelog does not contain a proper version update."
                              }
                              buildStages.deployPackage()

                              if (config?.skip_doc_build != true) {
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
