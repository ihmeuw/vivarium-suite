"""Release-matrix construction: dependency-ordered batch with per-entry PyPI waits."""

from __future__ import annotations

from collections.abc import Mapping

from .graph import get_transitive_upstreams, sort_topologically
from .models import Lib, ReleaseMatrix, ReleaseMatrixEntry, WaitForEntry


def get_release_matrix(
    release_versions: Mapping[str, str], libs: Mapping[str, Lib]
) -> ReleaseMatrix:
    """Build the dependency-ordered GitHub Actions release matrix.

    Given the libraries to release and their versions, returns a matrix object
    suitable for ``strategy.matrix`` in the release workflow. The ``include``
    entries are ordered dependencies-first (see :func:`sort_topologically`), and
    each entry carries the in-batch upstream libraries it must wait for on PyPI
    before it can install, i.e. the libraries it depends on that are *also* part
    of this release batch. Upstream libraries that are not in the batch are already
    released and so are omitted from ``wait_for``.

    Parameters
    ----------
    release_versions
        Mapping of library ``name`` to the version being released.
    libs
        The full set of parsed libraries.

    Returns
    -------
        A :class:`ReleaseMatrix`, ordered dependencies-first (see
        :func:`sort_topologically`) and empty when ``release_versions`` is empty.

    Raises
    ------
    DependencyCycleError
        If the release batch forms a dependency cycle.
    KeyError
        If a key of ``release_versions`` is not a key in ``libs``.
    """
    for lib_name in release_versions:
        if lib_name not in libs:
            raise KeyError(lib_name)

    batch = set(release_versions)
    ordered_lib_names = sort_topologically(list(release_versions), libs)

    include: list[ReleaseMatrixEntry] = []
    for lib_name in ordered_lib_names:
        upstreams = sort_topologically(
            sorted(get_transitive_upstreams(lib_name, libs) & batch), libs
        )
        wait_for: list[WaitForEntry] = [
            {"dist": libs[upstream].dist_name, "version": release_versions[upstream]}
            for upstream in upstreams
        ]
        include.append(
            {
                "library": lib_name,
                "dist": libs[lib_name].dist_name,
                "version": release_versions[lib_name],
                "wait_for": wait_for,
            }
        )
    return {"include": include}
