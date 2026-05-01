/**
 * Returns the package subdirectory for monorepo builds, or empty string for single-repo builds.
 *
 * JOB_NAME format for provisioned per-package pipelines:
 *   "<prefix>/<repo>/libs/<pkg>/<branch>"  e.g. "Public/vivarium-suite/libs/core/main"
 *
 * Returns "libs/<pkg>" (e.g. "libs/core") for monorepo builds, "" for single-repo builds.
 */
def call() {
    def parts = env.JOB_NAME.split('/')
    return (parts.length >= 5 && parts[2] == 'libs') ? "${parts[2]}/${parts[3]}" : ''
}
