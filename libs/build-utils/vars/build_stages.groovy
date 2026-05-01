def call() {
    // Return a map of functions that can be accessed
    return [
        runDebugInfo: this.&runDebugInfo,
        loadSharedFiles: this.&loadSharedFiles,
        buildEnvironment: this.&buildEnvironment,
        installPackage: this.&installPackage,
        checkFormatting: this.&checkFormatting,
        runTests: this.&runTests,
        buildDocs: this.&buildDocs,
        testDocs: this.&testDocs,
        deployPackage: this.&deployPackage,
        deployDocs: this.&deployDocs,
        cleanup: this.&cleanup,
        cleanupDebug: this.&cleanupDebug
    ]
}

// Individual function implementations
def runDebugInfo(Map skipEval = [:]) {
    stage("Debug Info - Python ${PYTHON_VERSION}") {

        echo "Jenkins pipeline run timestamp: ${env.TIMESTAMP}"
        
        // Display parameters used.
        echo """Parameters:
        SKIP_DEPLOY: ${params.SKIP_DEPLOY}
        RUN_SLOW: ${params.RUN_SLOW}
        RUN_WEEKLY: ${params.RUN_WEEKLY}
        SLACK_TO: ${params.SLACK_TO}
        DEBUG: ${params.DEBUG}
        FORCE_FULL_BUILD: ${params.FORCE_FULL_BUILD}"""

        // Display environment variables from Jenkins.
        echo """Environment:
        NODE_NAME:      '${NODE_NAME}'
        EXECUTOR_NUMBER: '${EXECUTOR_NUMBER}'
        ACTIVATE:       '${ACTIVATE}'
        BUILD_NUMBER:   '${BUILD_NUMBER}'
        BRANCH:         '${BRANCH}'
        CONDARC:        '${CONDARC}'
        CONDA_BIN_PATH: '${CONDA_BIN_PATH}'
        CONDA_ENV_NAME: '${CONDA_ENV_NAME}'
        CONDA_ENV_PATH: '${CONDA_ENV_PATH}'
        GIT_BRANCH:     '${GIT_BRANCH}'
        JOB_NAME:       '${JOB_NAME}'
        WORKSPACE:      '${WORKSPACE}'
        XDG_CACHE_HOME: '${XDG_CACHE_HOME}'
        IS_CRON:        '${IS_CRON}'
        CRON_SCHEDULE:  '${env.CRON_SCHEDULE}'
        GIT_COMMIT:     '${env.GIT_COMMIT}'
        GIT_PREVIOUS_COMMIT: '${env.GIT_PREVIOUS_COMMIT}'
        """
        
        // Display skip evaluation results (evaluated after checkout)
        if (skipEval) {
            echo """Skip Evaluation:
        previousBuildPassed:   ${skipEval.previousBuildPassed}
        isDocOnlyChange:       ${skipEval.isDocOnlyChange}
        isChangelogOnlyChange: ${skipEval.isChangelogOnlyChange}
        canSkipFullBuild:      ${skipEval.canSkipFullBuild}
        skipForDocOnly:        ${skipEval.skipForDocOnly}
        skipForChangelogOnly:  ${skipEval.skipForChangelogOnly}
        """
        }
    }
}

/**
 * Runs a closure in the package subdirectory for monorepo builds, or "." for single-repo builds.
 * Derives the subdirectory from JOB_NAME via get_package_subdir().
 */
def withWorkingDirectory(Closure body) {
    def subdir = get_package_subdir()
    dir(subdir ?: '.') {
        body()
    }
}

def loadSharedFiles() {
    stage("Load Shared Files") {
        withWorkingDirectory {
            load_shared_files()
        }
    }
}

def buildEnvironment() {
    stage("Build Environment - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            if (params.DEBUG) {
                echo "working directory: ${pwd()}"
                sh 'ls -la'
            }
            // The env should have been cleaned out after the last build, but delete it again
            // here just to be safe.
            sh "rm -rf ${CONDA_ENV_PATH}"
            sh "${env.ACTIVATE_BASE} && make create-env PYTHON_VERSION=${PYTHON_VERSION}"
            // open permissions for test users to create file in workspace
            sh "chmod 777 ${WORKSPACE}"
        }
    }
}

def installPackage(String env_reqs = "") {
    env_reqs = env_reqs ? "ENV_REQS=${env_reqs}" : ""
    stage("Install Package - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            sh "${ACTIVATE} && make install ${env_reqs} UV_FLAGS='--no-cache' && uv pip install . --extra-index-url https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/simple/ --index-strategy unsafe-best-match --no-cache"
        }
    }
}

def checkFormatting(Boolean run_mypy) {
    stage("Check Formatting - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            script {
                sh "${ACTIVATE} && make lint"
                if (run_mypy == true) {
                    sh "${ACTIVATE} && make mypy"
                }
            }
        }
    }
}

def runTests(List test_types, boolean runWeekly) {
    stage("Run Tests - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            script {
                def full_name = { test_type ->
                    if (test_type == 'e2e') {
                        return "End-to-End"
                    } else {
                        return test_type.capitalize()
                    }
                }
                def parallelTests = test_types.collectEntries {
                    ["${full_name(it)} Tests" : {
                        stage("Run ${full_name(it)} Tests - Python ${PYTHON_VERSION}") {
                            sh "${ACTIVATE} && make ${it}${(env.IS_CRON.toBoolean() || params.RUN_SLOW) ? ' RUNSLOW=1' : ''}${runWeekly ? ' RUNWEEKLY=1' : ''}"
                            // Map test target names to coverage directory names
                            // test-all -> htmlcov_tests, test-unit -> htmlcov_unit, etc.
                            def coverageDir = it == 'test-all' ? 'tests' : it.replace('test-', '')
                            publishHTML([
                                allowMissing: true,
                                alwaysLinkToLastBuild: false,
                                keepAll: true,
                                reportDir: "output/htmlcov_${coverageDir}",
                                reportFiles: "index.html",
                                reportName: "Coverage Report - ${full_name(it)} tests",
                                reportTitles: ''
                            ])
                        }
                    }]
                }
                parallel parallelTests
            }
        }
    }
}

def buildDocs() {
    stage("Build Docs - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            sh "${ACTIVATE} && make build-docs"
        }
    }
}

def testDocs() {
    stage("Test Docs - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            sh "${ACTIVATE} && make test-docs"
        }
    }
}

def deployPackage() {
    stage("Tagging Version and Pushing") {
        withWorkingDirectory {
            sh "${ACTIVATE} && make tag-version"
        }
    }

    stage("Build Package - Python ${PYTHON_VERSION}") {
        withWorkingDirectory {
            sh "${ACTIVATE} && make build-package"
        }
    }

    stage("Deploy Package to Artifactory") {
        withWorkingDirectory {
            withCredentials([usernamePassword(
                credentialsId: 'artifactory_simsci',
                usernameVariable: 'PYPI_ARTIFACTORY_CREDENTIALS_USR',
                passwordVariable: 'PYPI_ARTIFACTORY_CREDENTIALS_PSW'
            )]) {
                sh "${ACTIVATE} && make deploy-package-artifactory"
            }
        }
    }
}

def deployDocs() {
    stage("Deploy Docs") {
        withWorkingDirectory {
            withEnv(["DOCS_ROOT_PATH=/mnt/team/simulation_science/pub/docs"]) {
                sh "${ACTIVATE} && make deploy-docs"
            }
        }
    }
}

def cleanup() {
    withWorkingDirectory { sh "make clean" }
    // Remove the conda environment immediately to conserve local disk space.
    // Envs are built on local node storage, not shared NFS, so they must be
    // cleaned up by the build that created them.
    sh "${env.ACTIVATE_BASE} && conda env remove -p ${CONDA_ENV_PATH} --yes || rm -rf ${CONDA_ENV_PATH}"
    // cleanWs must run outside withWorkingDirectory: deleting the workspace while dir() is
    // still inside it causes a FileNotFoundException when dir() tries to restore its context.
    // deleteDirs: true ensures both WORKSPACE and WORKSPACE@tmp are cleaned.
    cleanWs(deleteDirs: true, disableDeferredWipeout: true)
}

def cleanupDebug() {
    // When DEBUG is enabled, only clean @tmp to preserve workspace for inspection
    withWorkingDirectory { sh "make clean" }
    sh "rm -rf '${WORKSPACE}@tmp'"
}
