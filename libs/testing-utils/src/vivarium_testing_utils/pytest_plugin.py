"""Pytest plugin providing common fixtures for vivarium projects.

This module is automatically loaded by pytest when vivarium_testing_utils is installed,
via the pytest11 entry point defined in setup.py.
"""

import pytest
from layered_config_tree import LayeredConfigTree
from pytest_mock import MockerFixture


@pytest.fixture
def no_gbd_cache(mocker: MockerFixture) -> None:
    """Disable vivarium_gbd_access caching for test isolation.

    This fixture mocks ``vivarium_gbd_access.utilities.get_input_config`` to return
    a configuration with ``cache_data`` set to False, ensuring that tests always
    pull fresh data rather than using cached results.

    Note that this fixture does NOT use ``autouse=True``. If you want it to apply
    to all tests in a module or package, create a wrapper fixture in your conftest.py:

    .. code-block:: python

        import pytest

        @pytest.fixture(autouse=True)
        def no_cache(no_gbd_cache):
            '''Apply no_gbd_cache to all tests in this module.'''
            pass

    """
    mocker.patch(
        "vivarium_gbd_access.utilities.get_input_config",
        return_value=LayeredConfigTree({"input_data": {"cache_data": False}}),
    )
