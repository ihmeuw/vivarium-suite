"""Version-string helpers shared across the build utilities."""

from __future__ import annotations


def version_key(version: str) -> tuple[int, ...]:
    """Return a numeric sort key so ``3.9`` orders before ``3.10``.

    Parameters
    ----------
    version
        A dotted numeric version string, e.g. ``"3.11"``.

    Returns
    -------
        The dotted parts as ints, for use as a ``sorted`` key.
    """
    return tuple(int(part) for part in version.split("."))
