/**
 * The step entry point. Provisions per-package Multibranch Pipeline jobs on Jenkins
 * for every Jenkinsfile path supplied.
 *
 * Must be called from the top-level monorepo Jenkinsfile (not a per-package pipeline).
 * The provisioner pipeline's JOB_NAME format is "<something>/<repo>/<branch>"; the
 * per-package pipelines it creates use "<prefix>/<repo>/libs/<pkg>/<branch>".
 *
 * @param config.jenkinsfiles  List of Jenkinsfile paths to provision (e.g. ["libs/core/Jenkinsfile"])
 * @param config.folderPrefix  Jenkins folder prefix for provisioned pipelines (default: "Public")
 */
def call(Map config = [:]) {
    echo "Provisioning Multibranch Pipelines for Libraries..."

    List<String> jenkinsfilePaths = config.jenkinsfiles ?: []
    if (jenkinsfilePaths.isEmpty()) {
        echo "No Jenkinsfile paths provided — skipping provisioning"
        return
    }

    // Use GIT_URL rather than JOB_NAME to derive the repository name reliably;
    // JOB_NAME format for the provisioner pipeline varies depending on whether it
    // lives inside a Jenkins Organization Folder.
    String repositoryName = env.GIT_URL.tokenize('/').last().replace('.git', '')
    String folderPrefix = config.folderPrefix ?: 'Public'
    String rootFolderPath = "${folderPrefix}/${repositoryName}"

    echo "Found ${jenkinsfilePaths.size()} Jenkinsfile(s)"
    echo "Repository Name: ${repositoryName}"
    echo "Root Folder Path: ${rootFolderPath}"
    jenkinsfilePaths.each { path -> echo "  - ${path}" }

    def provisionItems = { String rootPath, String repoURL, List<String> paths ->
        echo "Executing Job DSL to provision Jenkins items..."
        jobDsl(
            scriptText: libraryResource('multiPipelines.groovy'),
            additionalParameters: [
                jenkinsfilePathsStr: paths,
                rootFolderStr      : rootPath,
                repositoryURL      : repoURL
            ],
            // The following may be set to 'IGNORE'.
            // Note that because we only provision from the default branch (main),
            // branches will not compete to delete and recreate items.
            removedJobAction: 'DELETE'
        )
        echo "Job DSL execution completed"
    }

    provisionItems(rootFolderPath, env.GIT_URL, jenkinsfilePaths)
}
