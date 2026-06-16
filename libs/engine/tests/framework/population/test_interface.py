import pytest
from pytest_mock import MockerFixture

from vivarium.engine.framework.population.interface import PopulationInterface


@pytest.mark.xfail(reason="not implemented: PopulationInterface.get_all_attribute_names")
def test_population_interface_exposes_all_attribute_names(mocker: MockerFixture) -> None:
    """get_all_attribute_names delegates to the population manager."""
    manager = mocker.Mock()
    manager.get_all_attribute_names.return_value = ["age", "sex", "pregnant"]
    interface = PopulationInterface(manager)
    result = interface.get_all_attribute_names()
    assert result == ["age", "sex", "pregnant"]
    manager.get_all_attribute_names.assert_called_once_with()
