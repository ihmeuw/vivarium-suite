"""Time-bomb reminder to remove vivarium-compat once migrations are complete.

Fires after the deadline below. If you hit this:
  - If all downstream packages have migrated to the new import paths,
    delete libs/compat/ (see README for the full removal checklist).
  - If migrations are still in progress, bump the deadline and note why.

Note: this deadline is independent of
``libs/config-tree/tests/unit/test_removal_deadline.py``, which governs the
lifetime of the ``LayeredConfigTree`` -> ``ConfigTree`` deprecation alias only.
The two currently share a date but should not be assumed to move in lockstep -
update each one when its specific shim is actually retired.
"""

import datetime

import pytest

_REMOVAL_DEADLINE = datetime.date(2027, 7, 1)


def test_remove_by_deadline():
    if datetime.date.today() >= _REMOVAL_DEADLINE:
        pytest.fail(
            f"vivarium-compat hit its removal deadline ({_REMOVAL_DEADLINE}). "
            "If migrations are complete, delete libs/compat/ (see README). "
            "If not, bump _REMOVAL_DEADLINE in this file and document why."
        )
