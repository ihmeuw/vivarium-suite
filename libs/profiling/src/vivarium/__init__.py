"""Namespace package.

Merge all installed vivarium.* distributions into one namespace.

Notes
-----
- This file must be identical across every vivarium-* distribution.
- The extend_path line below is the only executable code allowed here.
- Additional logic belongs in subpackages (e.g. vivarium.<package>).
- Install order does not matter; extend_path accumulates __path__ entries from all of them.
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
