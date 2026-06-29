"""In-tree dependency DAG queries, backed by :mod:`networkx`.

The packages under ``libs/`` form a directed acyclic graph: one node per
package, an edge from each in-tree upstream to the dependent that requires it.
This module builds that graph from the parsed
:class:`~vivarium.build_utils._parsing.Lib` records and exposes the two queries
the cross-package flows need - transitive reachability and a dependencies-first
topological order (with cycle detection). The graph algorithms themselves are
delegated to ``networkx``; only the construction (mapping in-tree distribution
names back to package names) is local.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import networkx as nx

from vivarium.build_utils._parsing import Lib


class DependencyCycleError(Exception):
    """The in-tree dependency graph contains a cycle and cannot be ordered."""


def build_graph(libs: Mapping[str, Lib]) -> nx.DiGraph:
    """Build the in-tree dependency DAG.

    Adds one node per package (keyed by ``name``) and a directed edge
    ``upstream -> dependent`` for each in-tree dependency, carrying that
    dependency's :class:`~packaging.specifiers.SpecifierSet` as the ``specifier``
    edge attribute. The orientation (dependency points at its dependent) means a
    topological sort yields dependencies first and ``ancestors(node)`` is the set
    of packages ``node`` depends on.
    """
    dist_to_name = {lib.dist_name: name for name, lib in libs.items()}
    graph: nx.DiGraph = nx.DiGraph()
    graph.add_nodes_from(libs)
    for name, lib in libs.items():
        for dep_dist, specifier in lib.sibling_deps.items():
            upstream = dist_to_name.get(dep_dist)
            if upstream is not None and upstream != name:
                graph.add_edge(upstream, name, specifier=specifier)
    return graph


def get_reachable_siblings(target: str, libs: Mapping[str, Lib]) -> set[str]:
    """Return all in-tree package names transitively reachable from ``target``.

    Follows only in-tree edges; the result excludes ``target`` itself.

    Parameters
    ----------
    target
        Package ``name`` to compute reachability from.
    libs
        The full set of parsed packages.

    Returns
    -------
        Names of the in-tree packages reachable from ``target``.

    Raises
    ------
    KeyError
        If ``target`` is not a key in ``libs``.
    """
    if target not in libs:
        raise KeyError(target)
    # In the upstream -> dependent orientation, the packages ``target`` depends
    # on (transitively) are exactly its ancestors.
    return set(nx.ancestors(build_graph(libs), target))  # type: ignore[no-untyped-call]


def get_release_order(names: Sequence[str], libs: Mapping[str, Lib]) -> list[str]:
    """Topologically sort ``names`` dependencies-first.

    Orders only the packages in ``names`` relative to one another, using the
    in-tree edges among them; packages outside ``names`` are ignored. Among
    packages with no dependency relationship the input order is preserved.

    Parameters
    ----------
    names
        Package ``name``s to order.
    libs
        The full set of parsed packages.

    Returns
    -------
        ``names`` reordered so each package follows every package in ``names``
        it depends on.

    Raises
    ------
    DependencyCycleError
        If the packages in ``names`` form a dependency cycle.
    """
    # De-duplicate (defensive) and preserve input order; the index is the tie-break
    # key so independent packages keep their input order in the topological sort.
    unique = list(dict.fromkeys(names))
    order_index = {name: position for position, name in enumerate(unique)}
    batch = build_graph(libs).subgraph(unique)  # type: ignore[no-untyped-call]
    try:
        return list(nx.lexicographical_topological_sort(batch, key=order_index.__getitem__))
    except nx.NetworkXUnfeasible as error:
        members = sorted({node for edge in nx.find_cycle(batch) for node in edge[:2]})
        raise DependencyCycleError(f"dependency cycle among packages: {members}") from error
