def call(String nodeLabel = 'svc-simsci') {
    /* Gets the vivarium_build_utils version using the centralized script.

    The intent is that the Jenkinsfile in other repos that use vivarium_build_utils
    load this function in order to get the version of vivarium_build_utils
    to use for the pipeline.
    */

    def vbuVersion = null
    
    node(nodeLabel) {

        checkout scm

        load_bootstrap_scripts()
        
        vbuVersion = sh(
            script: 'python3 get_vbu_version.py',
            returnStdout: true
        ).trim()
        
        echo "Resolved vivarium_build_utils version: ${vbuVersion}"
    }
    
    return vbuVersion
}
