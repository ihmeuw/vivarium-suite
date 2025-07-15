// Extract the python versions in the downstream repository's python_versions.json file
// and return them as a list
import groovy.json.JsonSlurper

def call(workspace, git_url) {
    // parse the name of the repository from the git url

    def repo_name = git_url.tokenize("/")[-1].tokenize(".")[0]
    // raise an error if the file doesn't exist
    def filename = "${workspace}/python_versions.json"
    if (!fileExists(filename)) {
        error("python_versions.json file not found in repository ${repo_name}")
    }
    def python_versions_string = readFile filename
    return new JsonSlurper().parseText(python_versions_string)
}