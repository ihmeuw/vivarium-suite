"""Graph algorithms over the in-tree dependency graph (reachability, topo order)."""

from __future__ import annotations

import graphlib
from collections.abc import Iterable, Mapping, Sequence

from .models import DependencyCycleError, Lib


def get_transitive_downstreams(targets: Iterable[str], libs: Mapping[str, Lib]) -> set[str]:
    """Return all in-tree libraries that transitively depend on any of ``targets``.

    Inverts the ``upstreams`` edges and walks them outward from every target, so the
    result is the set of libraries a release of ``targets`` could break. The result
    excludes the targets themselves. Reachability tolerates cycles, so ``libs`` may be
    resolved over any extras (including the ``ci_github`` test-dep edges).

    Parameters
    ----------
    targets
        Library ``name``s whose dependents to compute.
    libs
        The full set of parsed libraries.

    Returns
    -------
        Names of the in-tree libraries downstream of ``targets``.

    Raises
    ------
    KeyError
        If any of ``targets`` is not a key in ``libs``.
    """
    targets = list(targets)
    for target in targets:
        if target not in libs:
            raise KeyError(target)
    dist_to_name = _get_dist_to_name_mapping(libs)

    # Reverse adjacency: each upstream name -> the libraries that depend on it.
    downstreams: dict[str, set[str]] = {name: set() for name in libs}
    for name, lib in libs.items():
        for dep_dist in lib.upstreams:
            dep_name = dist_to_name.get(dep_dist)
            if dep_name is not None:
                downstreams[dep_name].add(name)

    reached: set[str] = set()
    stack = list(targets)
    while stack:
        current = stack.pop()
        for dependent in downstreams.get(current, ()):
            if dependent not in reached:
                # A new dependent was found, so record it and keep walking outward.
                reached.add(dependent)
                stack.append(dependent)
    reached.difference_update(targets)
    return reached


def get_transitive_upstreams(target: str, libs: Mapping[str, Lib]) -> set[str]:
    """Return all in-tree library names transitively reachable from ``target``.

    Walks ``target``'s ``upstreams`` and those of every library it reaches,
    following only in-tree edges. The result excludes ``target`` itself.

    Parameters
    ----------
    target
        Library ``name`` to compute reachability from.
    libs
        The full set of parsed libraries.

    Returns
    -------
        Names of the in-tree libraries reachable from ``target``.

    Raises
    ------
    KeyError
        If ``target`` is not a key in ``libs``.
    """
    if target not in libs:
        raise KeyError(target)
    dist_to_name = _get_dist_to_name_mapping(libs)

    reached: set[str] = set()
    stack = [target]
    while stack:
        current = stack.pop()
        for dep_dist in libs[current].upstreams:
            dep_name = dist_to_name.get(dep_dist)
            if dep_name is not None and dep_name not in reached:
                # A new dependency was found, so add it to the reached set and push
                # it onto the stack for further exploration
                reached.add(dep_name)
                stack.append(dep_name)
    reached.discard(target)
    return reached


def sort_topologically(names: Sequence[str], libs: Mapping[str, Lib]) -> list[str]:
    """Topologically sort library ``names`` dependencies-first.

    Orders only the libraries in ``names`` relative to one another, using the
    in-tree edges among them; libraries outside ``names`` are ignored. Input order
    is preserved for libraries with no dependency relationships.

    Parameters
    ----------
    names
        Library ``name``s to order.
    libs
        The full set of parsed libraries.

    Returns
    -------
        Library ``names`` reordered dependencies-first, i.e. each library appears
        after all of its in-tree upstreams (that are also in ``names``).

    Raises
    ------
    DependencyCycleError
        If the libraries in ``names`` form a dependency cycle.
    """
    # De-duplicate (defensive) and preserve input order.
    sorted_names = list(dict.fromkeys(names))
    scope = set(sorted_names)

    # Add every node up front in input order followed by the edges
    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for name in sorted_names:
        sorter.add(name)
    for name in sorted_names:
        sorter.add(name, *_get_direct_upstreams(name, libs, scope=scope))

    try:
        # NOTE: static_order() yields zero-predecessor nodes in insertion order,
        # so independent libraries keep their input order (the only tie-break that
        # matters as it sets the GitHub Actions release matrix order).
        return list(sorter.static_order())
    except graphlib.CycleError as error:
        cycle = error.args[1]
        raise DependencyCycleError(
            f"dependency cycle among libraries: {sorted(set(cycle))}"
        ) from error


def _get_dist_to_name_mapping(libs: Mapping[str, Lib]) -> dict[str, str]:
    """Map each in-tree ``dist_name`` to its library ``name``."""
    return {lib.dist_name: name for name, lib in libs.items()}


def _get_direct_upstreams(name: str, libs: Mapping[str, Lib], scope: set[str]) -> set[str]:
    """Return ``name``'s direct in-tree upstream names that are in ``scope``."""
    dist_to_name = _get_dist_to_name_mapping(libs)
    upstreams: set[str] = set()
    for dep_dist in libs[name].upstreams:
        dep_name = dist_to_name.get(dep_dist)
        if dep_name in scope and dep_name != name:
            upstreams.add(dep_name)
    return upstreams
