// Extract the python versions in the downstream repository's python_versions.json file
// and return them as a list
import groovy.json.JsonSlurper

def call(workspace, git_url, String subdir = '') {
    def repo_name = git_url.tokenize("/")[-1].tokenize(".")[0]
    def filename = subdir ? "${workspace}/${subdir}/python_versions.json" : "${workspace}/python_versions.json"
    if (!fileExists(filename)) {
        error("python_versions.json not found at ${filename} in repository ${repo_name}")
    }
    return new JsonSlurper().parseText(readFile(filename))
}
