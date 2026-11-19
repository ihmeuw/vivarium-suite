import groovy.transform.Field

// The uv that CI uses. Pinned so a uv release cannot change how CI resolves or installs.
@Field final String UV_VERSION = "0.13.6"

/**
 * Install the pinned uv into dir with Astral's install script, and return the path to uv.
 *
 * UV_UNMANAGED_INSTALL puts uv and uvx in dir only. The script then writes no install
 * receipt and does not change PATH, so it leaves the service account's home alone.
 */
def call(String dir) {
    // The pipe hides a failed download from `sh -e`, so `uv --version` checks the install.
    sh """
        curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | env UV_UNMANAGED_INSTALL='${dir}' sh -s -- --quiet
        '${dir}/uv' --version
    """
    return "${dir}/uv"
}
