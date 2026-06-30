"""Release-matrix construction: dependency-ordered batch with per-entry PyPI waits."""

from __future__ import annotations

from collections.abc import Mapping

from .graph import get_reachable_siblings, get_release_order
from .models import Lib


def get_release_matrix(
    release_versions: Mapping[str, str], libs: Mapping[str, Lib]
) -> dict[str, object]:
    """Build the dependency-ordered GitHub Actions release matrix.

    Given the packages to release and their versions, returns a matrix object
    suitable for ``strategy.matrix`` in the release workflow. The ``include``
    entries are ordered dependencies-first (see :func:`get_release_order`), and
    each entry carries the in-batch upstreams it must wait for on PyPI before
    it can install - i.e. the packages it depends on that are *also* part of
    this release batch. Upstreams that are not in the batch are already
    released and so are omitted from ``wait_for``.

    Parameters
    ----------
    release_versions
        Mapping of package ``name`` to the version being released.
    libs
        The full set of parsed packages.

    Returns
    -------
        A dictionary suitable for ``strategy.matrix`` in the release workflow:
        ``{"include": [{"library": name, "version": version, "wait_for":
        [{"dist": dist_name, "version": version}, ...]}, ...]}``. ``include``
        is empty when ``release_versions`` is empty.

    Raises
    ------
    DependencyCycleError
        If the release batch forms a dependency cycle.
    KeyError
        If a key of ``release_versions`` is not a key in ``libs``.
    """
    for name in release_versions:
        if name not in libs:
            raise KeyError(name)

    batch = set(release_versions)
    ordered = get_release_order(list(release_versions), libs)

    include: list[dict[str, object]] = []
    for name in ordered:
        upstreams = get_release_order(
            sorted(get_reachable_siblings(name, libs) & batch), libs
        )
        wait_for = [
            {"dist": libs[upstream].dist_name, "version": release_versions[upstream]}
            for upstream in upstreams
        ]
        include.append(
            {
                "library": name,
                "version": release_versions[name],
                "wait_for": wait_for,
            }
        )
    return {"include": include}
