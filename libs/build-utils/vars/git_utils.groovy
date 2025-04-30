def getPreviousCommit() {
    return sh(
        script: "git rev-parse HEAD~1",
        returnStdout: true
    ).trim()
}

def getCurrentCommit() {
    return sh(
        script: "git rev-parse HEAD",
        returnStdout: true
    ).trim()
}

def getChangedFiles(previousCommit, currentCommit) {
    return sh(
        script: "git diff --name-only ${previousCommit} ${currentCommit} || echo ''",
        returnStdout: true
    ).trim()
}

// Combined function to get both commits and changed files
def getCommitInfo() {
    def previousCommit = getPreviousCommit()
    def currentCommit = getCurrentCommit()
    def changedFiles = getChangedFiles(previousCommit, currentCommit)
    
    return [
        previousCommit: previousCommit,
        currentCommit: currentCommit,
        changedFiles: changedFiles
    ]
}