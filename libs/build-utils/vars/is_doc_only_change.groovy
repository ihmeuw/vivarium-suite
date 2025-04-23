def call() {
    // Check if this is a PR build by looking for CHANGE_TARGET
    if (env.CHANGE_TARGET) {
        // Fetch to ensure we get target branch
        sh(script: "git fetch --no-tags --force --progress ${env.GIT_URL} +refs/heads/${env.CHANGE_TARGET}:refs/remotes/origin/${env.CHANGE_TARGET}", returnStdout: false)

        def changedFiles = sh(
            script: "git diff --name-only origin/${env.CHANGE_TARGET} || echo ''",
            returnStdout: true
        ).trim()
        // If no files are found (which shouldn't happen), return false
        if (changedFiles == '') {
            return false
        }

        // Check if all changed files are within the docs/ directory
        def hasNonDocChanges = sh(
            script: """
                git diff --name-only origin/${env.CHANGE_TARGET} |
                grep -v '^docs/' |
                wc -l || echo '0'
            """,
            returnStdout: true
        ).trim().toInteger() > 0
        // Return true if there are no non-doc changes
        return !hasNonDocChanges
    } else {
        // Not a PR build, so return false
        return false
    }
}
