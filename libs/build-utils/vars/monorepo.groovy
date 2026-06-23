/**
 * The step entry point. Provisions per-package Multibranch Pipeline jobs on Jenkins
 * for every Jenkinsfile path supplied.
 *
 * Must be called from the top-level monorepo Jenkinsfile (not a per-package pipeline).
 * The provisioner pipeline's JOB_NAME shape varies by Jenkins folder layout (e.g.
 * whether it sits inside a Jenkins Organization Folder); provisioned per-package
 * pipelines always use "<prefix>/<repo>/libs/<pkg>/<branch>".
 *
 * @param config.jenkinsfiles          (required) List of Jenkinsfile paths to provision
 *                                     (e.g. ["libs/core/Jenkinsfile"])
 * @param config.githubCredentialsId   (required) Jenkins credential ID for the GitHub App
 *                                     used by the branch source. Lives in the calling
 *                                     monorepo's Jenkinsfile so vbu stays org-agnostic.
 * @param config.folderPrefix          Jenkins folder prefix for provisioned pipelines
 *                                     (default: "Public")
 */
def call(Map config = [:]) {
    echo "Provisioning Multibranch Pipelines for Libraries..."

    // Provisioning is main-only
    //   Why? If not, then when a feature branch (not main) omits or renames a Jenkinsfile
    //   that main still has, that pipeline would get deleted (because we call
    //   ``removedJobAction: 'DELETE'`` below) and then the next main build would
    //   re-create it; this would cycle over and over.
    //   Restricting to main defines the source of truth for "what libs exist".
    if (env.BRANCH_NAME != 'main') {
        echo "monorepo(): skipping provisioning (BRANCH_NAME='${env.BRANCH_NAME}', provisioning is main-only)"
        return
    }

    List<String> jenkinsfilePaths = config.jenkinsfiles ?: []
    if (jenkinsfilePaths.isEmpty()) {
        echo "No Jenkinsfile paths provided - skipping provisioning"
        return
    }

    String githubCredentialsId = config.githubCredentialsId
    if (!githubCredentialsId) {
        error("monorepo(): 'githubCredentialsId' is required. Pass the Jenkins credential ID for the GitHub App from the calling Jenkinsfile.")
    }

    // Use GIT_URL rather than JOB_NAME to derive the repository name reliably
    String repositoryName = env.GIT_URL.tokenize('/').last().replace('.git', '')
    String folderPrefix = config.folderPrefix ?: 'Public'
    String rootFolderPath = "${folderPrefix}/${repositoryName}"

    echo "Found ${jenkinsfilePaths.size()} Jenkinsfile(s)"
    echo "Repository Name: ${repositoryName}"
    echo "Root Folder Path: ${rootFolderPath}"
    jenkinsfilePaths.each { path -> echo "  - ${path}" }

    def provisionItems = { String rootPath, String repoURL, List<String> paths, String credId ->
        echo "Executing Job DSL to provision Jenkins items..."
        jobDsl(
            scriptText: libraryResource('multiPipelines.groovy'),
            additionalParameters: [
                jenkinsfilePathStrings: paths,
                rootFolderStr         : rootPath,
                repositoryURL         : repoURL,
                githubCredentialsId   : credId
            ],
            // Delete (instead of 'IGNORE') any libs/<pkg>/Jenkinsfile that's *missing*
            // from the current checkout (relative to a previous run). 
            removedJobAction: 'DELETE'
        )
        echo "Job DSL execution completed"
    }

    provisionItems(rootFolderPath, env.GIT_URL, jenkinsfilePaths, githubCredentialsId)
}
