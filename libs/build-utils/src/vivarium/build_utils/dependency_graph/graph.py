"""Graph algorithms over the in-tree dependency graph (reachability, topo order)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import DependencyCycleError, Lib


def _get_dist_to_name_mapping(libs: Mapping[str, Lib]) -> dict[str, str]:
    """Map each in-tree ``dist_name`` to its package ``name``."""
    return {lib.dist_name: name for name, lib in libs.items()}


def _get_in_scope_upstreams(name: str, libs: Mapping[str, Lib], scope: set[str]) -> set[str]:
    """Return ``name``'s direct in-tree upstream names that are in ``scope``."""
    dist_to_name = _get_dist_to_name_mapping(libs)
    upstreams: set[str] = set()
    for dep_dist in libs[name].sibling_deps:
        dep_name = dist_to_name.get(dep_dist)
        if dep_name in scope and dep_name != name:
            upstreams.add(dep_name)
    return upstreams


def get_reachable_siblings(target: str, libs: Mapping[str, Lib]) -> set[str]:
    """Return all in-tree package names transitively reachable from ``target``.

    Walks ``target``'s ``sibling_deps`` and those of every package it reaches,
    following only in-tree edges. The result excludes ``target`` itself.

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
    dist_to_name = _get_dist_to_name_mapping(libs)

    reached: set[str] = set()
    stack = [target]
    while stack:
        current = stack.pop()
        for dep_dist in libs[current].sibling_deps:
            dep_name = dist_to_name.get(dep_dist)
            if dep_name is not None and dep_name not in reached:
                # A new dependency was found, so add it to the reached set and push
                # it onto the stack for further exploration
                reached.add(dep_name)
                stack.append(dep_name)
    reached.discard(target)
    return reached


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
                f"dependency cycle among packages: {sorted(remaining)}"
            )
    return ordered
