def call() {
    // Define the patterns to exclude (files that don't count as deployable changes)
    def excludePatterns = [
        'Jenkinsfile',
        'Makefile',
        '\\.gitignore',
        '\\.github/.*'
    ]
    
    // Get the previous commit hash
    def commitInfo = git_utils.getCommitInfo()
    def previousCommit = commitInfo.previousCommit
    def currentCommit = commitInfo.currentCommit
    def changedFiles = commitInfo.changedFiles
    
    // If no files changed, return false
    if (changedFiles == '') {
        echo "No files were changed in this commit"
        return false
    }
    
    // Join the patterns with pipe character for grep -E
    def grepPattern = excludePatterns.join('|')
    
    // Count the number of changed files that don't match the exclude patterns
    def deployableChangeCount = sh(
        script: """
            git diff --name-only ${previousCommit} ${currentCommit} |
            grep -v -E '${grepPattern}' |
            wc -l || echo '0'
        """,
        returnStdout: true
    ).trim().toInteger()
    
    // If there are deployable changes, return true
    if (deployableChangeCount > 0) {
        echo "Found ${deployableChangeCount} deployable changes"
        return true
    } else {
        echo "No deployable changes found (only excluded files were modified)"
        return false
    }
}