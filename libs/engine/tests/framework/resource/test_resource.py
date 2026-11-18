import pickle
from types import ModuleType

import dill
import pytest

from tests.helpers import ColumnCreator
from vivarium.engine.framework.resource import Resource
from vivarium.engine.framework.resource.resource import ResourceId


def test_resource_id() -> None:
    resource = Resource("test", ColumnCreator())
    assert resource.resource_id == "generic_resource.test"


@pytest.mark.parametrize("pickler", [pickle, dill])
def test_resource_id_pickle_round_trip(pickler: ModuleType) -> None:
    resource_id = ResourceId("attribute", "age")
    loaded = pickler.loads(pickler.dumps(resource_id))
    assert type(loaded) is ResourceId
    assert loaded == "attribute.age"
    assert loaded.resource_type == "attribute"
    assert loaded.name == "age"
