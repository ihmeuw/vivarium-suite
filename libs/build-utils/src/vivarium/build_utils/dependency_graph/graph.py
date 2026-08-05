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
    return _walk(targets, _build_reverse_adjacency(libs))


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
    return _walk([target], _build_forward_adjacency(libs))


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

    # Built once, not per name: the adjacency is the same for every edge lookup below.
    adjacency = _build_forward_adjacency(libs)

    # Add every node up front in input order followed by the edges
    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for name in sorted_names:
        sorter.add(name)
    for name in sorted_names:
        # Only edges among ``names`` matter; upstreams outside the scope are ignored.
        in_scope = {up for up in adjacency[name] if up in scope and up != name}
        sorter.add(name, *in_scope)

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


def _build_forward_adjacency(libs: Mapping[str, Lib]) -> dict[str, set[str]]:
    """Map each library name to the in-tree library names it depends on.

    The only place ``dist_name`` is translated back to a library ``name``: a
    :class:`Lib` records its upstreams by distribution name, while the graph is
    keyed by directory name. Upstreams that are not in-tree are dropped.
    """
    dist_to_name = {lib.dist_name: name for name, lib in libs.items()}
    adjacency: dict[str, set[str]] = {name: set() for name in libs}
    for name, lib in libs.items():
        for dep_dist in lib.upstreams:
            dep_name = dist_to_name.get(dep_dist)
            if dep_name is not None:
                adjacency[name].add(dep_name)
    return adjacency


def _build_reverse_adjacency(libs: Mapping[str, Lib]) -> dict[str, set[str]]:
    """Map each library name to the in-tree library names that depend on it."""
    reverse: dict[str, set[str]] = {name: set() for name in libs}
    for name, upstreams in _build_forward_adjacency(libs).items():
        for upstream in upstreams:
            reverse[upstream].add(name)
    return reverse


def _walk(starts: Iterable[str], adjacency: Mapping[str, set[str]]) -> set[str]:
    """Return every node reachable from ``starts``, excluding ``starts`` themselves.

    A stack-based traversal that visits each node once, so it terminates on a cyclic
    graph. Direction is the caller's choice of ``adjacency``.
    """
    starts = list(starts)
    reached: set[str] = set()
    stack = list(starts)
    while stack:
        for neighbor in adjacency.get(stack.pop(), ()):
            if neighbor not in reached:
                # Newly reached, so record it and keep walking outward from it.
                reached.add(neighbor)
                stack.append(neighbor)
    reached.difference_update(starts)
    return reached
