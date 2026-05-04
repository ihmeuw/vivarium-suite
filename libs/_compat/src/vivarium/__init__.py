"""Namespace package

Merge all installed vivarium.* distributions into one namespace.
Every vivarium-* package has this identical line.
Install order doesn't matter; extend_path accumulates __path__ entries from all of them.
No package should add anything else here; logic belongs in subpackages (e.g. vivarium._compat).
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
