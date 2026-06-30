"""Graph algorithms over the in-tree dependency graph (reachability, topo order)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import DependencyCycleError, Lib


def get_reachable_upstreams(target: str, libs: Mapping[str, Lib]) -> set[str]:
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


def get_release_order(names: Sequence[str], libs: Mapping[str, Lib]) -> list[str]:
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
    # De-duplicate (defensive) and preserve input order
    # NOTE: the ordering of ``sorted_names`` below does not matter for topological correctness;
    # it only affects tie-breaks as well as the github actions release matrix order.
    sorted_names = list(dict.fromkeys(names))

    deps: dict[str, set[str]] = {
        name: _get_in_scope_upstreams(name, libs, scope=set(sorted_names))
        for name in sorted_names
    }

    ordered: list[str] = []
    placed: set[str] = set()
    while len(ordered) < len(sorted_names):
        progressed = False
        for name in sorted_names:
            if name in placed:
                continue
            if deps[name] <= placed:
                ordered.append(name)
                placed.add(name)
                progressed = True
        if not progressed:
            remaining = [n for n in sorted_names if n not in placed]
            raise DependencyCycleError(
                f"dependency cycle among libraries: {sorted(remaining)}"
            )
    return ordered


def _get_dist_to_name_mapping(libs: Mapping[str, Lib]) -> dict[str, str]:
    """Map each in-tree ``dist_name`` to its library ``name``."""
    return {lib.dist_name: name for name, lib in libs.items()}


def _get_in_scope_upstreams(name: str, libs: Mapping[str, Lib], scope: set[str]) -> set[str]:
    """Return ``name``'s direct in-tree upstream names that are in ``scope``."""
    dist_to_name = _get_dist_to_name_mapping(libs)
    upstreams: set[str] = set()
    for dep_dist in libs[name].upstreams:
        dep_name = dist_to_name.get(dep_dist)
        if dep_name in scope and dep_name != name:
            upstreams.add(dep_name)
    return upstreams
