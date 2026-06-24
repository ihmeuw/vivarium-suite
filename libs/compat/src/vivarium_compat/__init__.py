"""Backward-compatible import redirects for the vivarium monorepo migration.

This package is intentionally a *top-level* package (not under the ``vivarium``
namespace) so that the ``vivarium_compat.pth`` startup file can install the
import-redirect hook without triggering ``vivarium/__init__.py``. If this lived
under ``vivarium``, the .pth load would import ``vivarium`` first, run its
package init, and try to import the very names this hook exists to redirect -
a chicken-and-egg deadlock.

See ``vivarium_compat._compat`` for the hook implementation and ``_REDIRECTS`` table.
"""
