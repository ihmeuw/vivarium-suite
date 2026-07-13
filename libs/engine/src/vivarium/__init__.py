"""``vivarium`` namespace package.

vivarium-engine owns this ``__init__.py``. The ``extend_path`` call lets
sibling distributions (vivarium-artifact, vivarium-config-tree, etc.)
contribute their own subpackages under ``vivarium.*``.
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
