"""Time-bomb reminder to remove the ``LayeredConfigTree`` deprecation alias.

Fires after the deadline below. If you hit this:
  - If no downstream code still imports ``LayeredConfigTree``, delete the
    ``__getattr__`` shim from ``vivarium/config_tree/__init__.py`` (and
    delete this test).
  - If migrations are still in progress, bump the deadline and note why.

The alias exists in ``vivarium/config_tree/__init__.py`` as a ``__getattr__``
hook that returns ``ConfigTree`` with a ``DeprecationWarning`` when callers
import the old name. See the v5.0.0 CHANGELOG entry for context.

Note: this deadline is independent of ``libs/compat/tests/test_removal_deadline.py``,
which governs the lifetime of the entire ``vivarium-compat`` package (a separate
transitional artifact). The two currently share a date but should not be assumed
to move in lockstep - update each one when its specific shim is actually retired.
"""

import datetime
import warnings

import pytest

import vivarium.config_tree as config_tree_module

_REMOVAL_DEADLINE = datetime.date(2027, 7, 1)


def test_remove_layered_config_tree_alias_by_deadline() -> None:
    if datetime.date.today() < _REMOVAL_DEADLINE:
        pytest.skip(f"Deadline ({_REMOVAL_DEADLINE}) has not been reached.")

    # Past the deadline: check whether the alias is still exposed.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        alias_present = hasattr(config_tree_module, "LayeredConfigTree")

    if alias_present:
        pytest.fail(
            f"LayeredConfigTree deprecation alias is still exposed past its removal "
            f"deadline ({_REMOVAL_DEADLINE}). If downstream code has migrated to "
            f"ConfigTree, delete the __getattr__ shim from "
            f"vivarium/config_tree/__init__.py (and this test). Otherwise bump "
            f"_REMOVAL_DEADLINE in this file and document why."
        )
