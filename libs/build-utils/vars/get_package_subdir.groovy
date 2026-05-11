/**
 * Returns the package subdirectory for monorepo builds, or empty string for single-repo builds.
 *
 * MONOREPO LAYOUT CONVENTION
 * --------------------------
 * vivarium_build_utils assumes every monorepo package lives at:
 *     <repo-root>/libs/<pkg>/
 * with a Jenkinsfile at <repo-root>/libs/<pkg>/Jenkinsfile. The literal segment
 * "libs" is the agreed convention shared across this file, the top-level
 * Jenkinsfile's monorepo() call (whose `jenkinsfiles` list points at
 * libs/<pkg>/Jenkinsfile paths), and the provisioned Jenkins folder hierarchy.
 * Any new monorepo adopting vbu MUST follow this layout.
 *
 * JOB_NAME format for provisioned per-package pipelines:
 *   "<prefix>/<repo>/libs/<pkg>/<branch>"  e.g. "Public/vivarium-suite/libs/core/main"
 *
 * Why JOB_NAME (not GIT_URL): per-package pipelines run inside the structure
 * provisioned by monorepo.groovy, so their JOB_NAME shape is guaranteed to
 * include "libs/<pkg>". monorepo.groovy itself parses GIT_URL instead because
 * the top-level provisioner pipeline's JOB_NAME varies (e.g. whether it sits
 * inside a Jenkins Organization Folder) and can't be relied on.
 *
 * Returns "libs/<pkg>" (e.g. "libs/core") for monorepo builds, "" for single-repo builds.
 */
def call() {
    def parts = env.JOB_NAME.split('/')
    return (parts.length >= 5 && parts[2] == 'libs') ? "${parts[2]}/${parts[3]}" : ''
}
