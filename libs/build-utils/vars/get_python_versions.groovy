// Extract the python versions in the downstream repository's python_versions.json file
// and return them as a list.
//
// @param workspace  Absolute path to the Jenkins workspace (e.g. env.WORKSPACE).
// @param git_url    Repository URL; used only for error messages.
// @param subdir     Optional path under the workspace to look in. Empty (the default)
//                   targets <workspace>/python_versions.json, which is correct for
//                   standalone repos. Monorepo per-package builds pass "libs/<pkg>"
//                   (see get_package_subdir.groovy) so the per-package
//                   python_versions.json is picked up instead of the monorepo root.
import groovy.json.JsonSlurper

def call(workspace, git_url, String subdir = '') {
    def repo_name = git_url.tokenize("/")[-1].tokenize(".")[0]
    def filename = subdir ? "${workspace}/${subdir}/python_versions.json" : "${workspace}/python_versions.json"
    if (!fileExists(filename)) {
        error("python_versions.json not found at ${filename} in repository ${repo_name}")
    }
    return new JsonSlurper().parseText(readFile(filename))
}
