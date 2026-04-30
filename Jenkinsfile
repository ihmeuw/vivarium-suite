/* Loads vivarium_build_utils as a Jenkins shared library and calls monorepo()
   to auto-provision a Multibranch Pipeline for each libs/ package.

   Two shared libraries must be configured in Jenkins (Manage Jenkins > Configure System
   > Global Pipeline Libraries):
     - "get_vbu_version" pointing at vivarium_build_utils/bootstrap/ on branch "main"
     - "vivarium_build_utils" pointing at vivarium_build_utils/ (branch resolved at runtime)

   Adding a new package under libs/ is picked up automatically on the next run.
*/

// Load the full vivarium_build_utils library at the expected version
// Note that vivarium-suite is not a python package and so we do not attempt to
// determine a non-main specific version of vivarium_build_utils.
library("vivarium_build_utils@main")

pipeline {
    agent any

    options {
        timestamps()
    }

    stages {
        stage('Multi-Multibranch Pipeline') {
            when { branch 'main' }
            steps {
                script {
                    def jenkinsfiles = findFiles(glob: 'libs/*/Jenkinsfile').collect { it.path }
                    // 'Public' targets the Jenkins folder where public monorepo jobs are provisioned
                    monorepo(jenkinsfiles: jenkinsfiles, folderPrefix: 'Public')
                }
            }
        }
    }
}
