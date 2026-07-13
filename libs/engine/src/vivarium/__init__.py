"""``vivarium`` namespace package.

vivarium-engine owns this ``__init__.py``. The ``extend_path`` call lets
sibling distributions (vivarium-artifact, vivarium-config-tree, etc.)
contribute their own subpackages under ``vivarium.*``.

Nothing else lives here: post-MIC-7100 the pre-monorepo attribute-import
shims (``from vivarium import Component`` etc.) have been removed.
Callers must reach for the canonical module - ``vivarium.engine`` for
``Component``, ``Observer``, ``InteractiveContext``, and
``build_model_specification``; ``vivarium.artifact`` for ``Artifact``.
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
