def call() {
    // Check if the previous build completed successfully.
    // Returns true if the previous build passed.
    // Returns false if the previous build doesn't exist, failed, was aborted, or is unstable.
    //
    // This is used to determine whether we can safely skip build steps for
    // doc-only or changelog-only changes. If the previous build failed, we
    // should run the full build to ensure the failure is addressed.
    
    def previousBuild = currentBuild.previousBuild
    
    if (previousBuild == null) {
        // No previous build exists, so we should run the entire build.
        echo "No previous build found. Treating as failure."
        return false
    }
    
    def previousResult = previousBuild.result
    echo "Previous build (#${previousBuild.number}) result: ${previousResult}"
    
    // SUCCESS is the only result we consider as "passed"
    // Other results include: FAILURE, UNSTABLE, ABORTED, NOT_BUILT, null.
    if (previousResult == 'SUCCESS') {
        return true
    } else {
        echo "Previous build did not pass (result: ${previousResult}). Will not skip build steps."
        return false
    }
}
