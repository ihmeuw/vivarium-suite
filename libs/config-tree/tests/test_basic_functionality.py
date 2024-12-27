import pickle
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from layered_config_tree import (
    ConfigNode,
    ConfigurationError,
    ConfigurationKeyError,
    DuplicatedConfigurationError,
    LayeredConfigTree,
)


@pytest.fixture(params=list(range(1, 5)))
def layers(request: pytest.FixtureRequest) -> list[str]:
    return [f"layer_{i}" for i in range(1, request.param + 1)]


@pytest.fixture
def layers_and_values(layers: list[str]) -> dict[str, str]:
    return {layer: f"test_value_{i+1}" for i, layer in enumerate(layers)}


@pytest.fixture
def empty_node(layers: list[str]) -> ConfigNode:
    return ConfigNode(layers, name="test_node")


@pytest.fixture
def full_node(layers_and_values: dict[str, str]) -> ConfigNode:
    n = ConfigNode(list(layers_and_values.keys()), name="test_node")
    for layer, value in layers_and_values.items():
        n.update(value, layer, source=None)
    return n


@pytest.fixture
def empty_tree(layers: list[str]) -> LayeredConfigTree:
    return LayeredConfigTree(layers=layers)


@pytest.fixture
def getter_dict() -> dict[str, Any]:
    return {
        "outer_layer_1": "test_value",
        "outer_layer_2": {"inner_layer": "test_value2"},
    }


def test_node_creation(empty_node: ConfigNode) -> None:
    assert not empty_node
    assert not empty_node.accessed
    assert not empty_node.metadata
    assert not repr(empty_node)
    assert not str(empty_node)


def test_full_node_update(full_node: ConfigNode) -> None:
    assert full_node
    assert not full_node.accessed
    assert len(full_node.metadata) == len(full_node._layers)
    assert repr(full_node)
    assert str(full_node)


def test_node_update_no_args() -> None:
    n = ConfigNode(["base"], name="test_node")
    n.update("test_value", layer=None, source="some_source")
    assert n._values["base"] == ("some_source", "test_value")

    n = ConfigNode(["layer_1", "layer_2"], name="test_node")
    n.update("test_value", layer=None, source="some_source")
    assert "layer_1" not in n._values
    assert n._values["layer_2"] == ("some_source", "test_value")


def test_node_update_with_args() -> None:
    cn = ConfigNode(["base"], name="test_node")
    cn.update("test_value", layer=None, source="test")
    assert cn._values["base"] == ("test", "test_value")

    cn = ConfigNode(["base"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    assert cn._values["base"] == ("test", "test_value")

    cn = ConfigNode(["layer_1", "layer_2"], name="test_node")
    cn.update("test_value", layer=None, source="test")
    assert "layer_1" not in cn._values
    assert cn._values["layer_2"] == ("test", "test_value")

    cn = ConfigNode(["layer_1", "layer_2"], name="test_node")
    cn.update("test_value", layer="layer_1", source="test")
    assert "layer_2" not in cn._values
    assert cn._values["layer_1"] == ("test", "test_value")

    cn = ConfigNode(["layer_1", "layer_2"], name="test_node")
    cn.update("test_value", layer="layer_2", source="test")
    assert "layer_1" not in cn._values
    assert cn._values["layer_2"] == ("test", "test_value")

    cn = ConfigNode(["layer_1", "layer_2"], name="test_node")
    cn.update("test_value", layer="layer_1", source="test")
    cn.update("test_value", layer="layer_2", source="test")
    assert cn._values["layer_1"] == ("test", "test_value")
    assert cn._values["layer_2"] == ("test", "test_value")


def test_node_frozen_update() -> None:
    cn = ConfigNode(["base"], name="test_node")
    cn.freeze()
    with pytest.raises(ConfigurationError):
        cn.update("test_val", layer=None, source=None)


def test_node_bad_layer_update() -> None:
    cn = ConfigNode(["base"], name="test_node")
    with pytest.raises(ConfigurationKeyError):
        cn.update("test_value", layer="layer_1", source=None)


def test_node_duplicate_update() -> None:
    cn = ConfigNode(["base"], name="test_node")
    cn.update("test_value", layer=None, source=None)
    with pytest.raises(DuplicatedConfigurationError):
        cn.update("test_value", layer=None, source=None)


def test_node_get_value_with_source_empty(empty_node: ConfigNode) -> None:
    with pytest.raises(ConfigurationKeyError):
        empty_node._get_value_with_source(layer=None)

    for layer in empty_node._layers:
        with pytest.raises(ConfigurationKeyError):
            empty_node._get_value_with_source(layer=layer)

    assert not empty_node.accessed


def test_node_get_value_with_source(full_node: ConfigNode) -> None:
    assert full_node._get_value_with_source(layer=None) == (
        None,
        f"test_value_{len(full_node._layers)}",
    )

    for i, layer in enumerate(full_node._layers):
        assert full_node._get_value_with_source(layer=layer) == (
            None,
            f"test_value_{i+1}",
        )

    assert not full_node.accessed


def test_node_get_value_empty(empty_node: ConfigNode) -> None:
    with pytest.raises(ConfigurationKeyError):
        empty_node.get_value()

    for layer in empty_node._layers:
        with pytest.raises(ConfigurationKeyError):
            empty_node.get_value()

    assert not empty_node.accessed


def test_node_get_value(full_node: ConfigNode) -> None:
    assert full_node.get_value() == f"test_value_{len(full_node._layers)}"
    assert full_node.accessed
    full_node._accessed = False

    assert full_node.get_value(layer=None) == f"test_value_{len(full_node._layers)}"
    assert full_node.accessed
    full_node._accessed = False

    for i, layer in enumerate(full_node._layers):
        assert full_node.get_value(layer=layer) == f"test_value_{i + 1}"
        assert full_node.accessed
        full_node._accessed = False

    assert not full_node.accessed


def test_node_repr() -> None:
    cn = ConfigNode(["base"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    expected_str = """\
        base: test_value
            source: test"""
    assert repr(cn) == textwrap.dedent(expected_str)

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    expected_str = """\
        base: test_value
            source: test"""
    assert repr(cn) == textwrap.dedent(expected_str)

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer=None, source="test")
    expected_str = """\
        layer_1: test_value
            source: test"""
    assert repr(cn) == textwrap.dedent(expected_str)

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    cn.update("test_value", layer="layer_1", source="test")
    expected_str = """\
        layer_1: test_value
            source: test
        base: test_value
            source: test"""
    assert repr(cn) == textwrap.dedent(expected_str)


def test_node_str() -> None:
    cn = ConfigNode(["base"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    expected_str = "base: test_value"
    assert str(cn) == expected_str

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    expected_str = "base: test_value"
    assert str(cn) == expected_str

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer=None, source="test")
    expected_str = "layer_1: test_value"
    assert str(cn) == expected_str

    cn = ConfigNode(["base", "layer_1"], name="test_node")
    cn.update("test_value", layer="base", source="test")
    cn.update("test_value", layer="layer_1", source="test")
    expected_str = "layer_1: test_value"
    assert str(cn) == expected_str


def test_tree_creation(empty_tree: LayeredConfigTree) -> None:
    assert len(empty_tree) == 0
    assert not empty_tree.items()
    assert not empty_tree.values()
    assert not empty_tree.keys()
    assert not repr(empty_tree)
    assert not str(empty_tree)
    assert not empty_tree._children
    assert empty_tree.to_dict() == {}


def test_tree_coerce_dict() -> None:
    data: dict[str, Any]
    data = {}
    src = "test"
    assert LayeredConfigTree._coerce(data, src) == (data, src)
    data = {"key": "val"}
    assert LayeredConfigTree._coerce(data, src) == (data, src)
    data = {"key1": {"sub_key1": ["val", "val", "val"], "sub_key2": "val"}, "key2": "val"}
    assert LayeredConfigTree._coerce(data, src) == (data, src)


def test_tree_coerce_str() -> None:
    src = "test"
    data = """\
    key: val"""
    assert LayeredConfigTree._coerce(data, src) == ({"key": "val"}, src)
    data = """\
    key1:
        sub_key1:
            - val
            - val
            - val
        sub_key2: val
    key2: val"""
    expected_dict = {
        "key1": {"sub_key1": ["val", "val", "val"], "sub_key2": "val"},
        "key2": "val",
    }
    assert LayeredConfigTree._coerce(data, src) == (expected_dict, src)
    data = """\
        key1:
            sub_key1: [val, val, val]
            sub_key2: val
        key2: val"""
    expected_dict = {
        "key1": {"sub_key1": ["val", "val", "val"], "sub_key2": "val"},
        "key2": "val",
    }
    assert LayeredConfigTree._coerce(data, src) == (expected_dict, src)


def test_tree_coerce_yaml(tmp_path: Path) -> None:
    data_to_write = """\
     key1:
         sub_key1:
             - val
             - val
             - val
         sub_key2: [val, val]
     key2: val"""
    expected_dict = {
        "key1": {"sub_key1": ["val", "val", "val"], "sub_key2": ["val", "val"]},
        "key2": "val",
    }
    src = "test"
    model_spec_path = tmp_path / "model_spec.yaml"
    with model_spec_path.open("w") as f:
        f.write(data_to_write)
    assert LayeredConfigTree._coerce(str(model_spec_path), src) == (expected_dict, src)
    assert LayeredConfigTree._coerce(str(model_spec_path), None) == (
        expected_dict,
        str(model_spec_path),
    )


def test_single_layer() -> None:
    lct = LayeredConfigTree()
    lct.update({"test_key": "test_value", "test_key2": "test_value2"})

    assert lct.test_key == "test_value"
    assert lct.test_key2 == "test_value2"

    with pytest.raises(DuplicatedConfigurationError):
        lct.test_key2 = "test_value3"

    assert lct.test_key2 == "test_value2"
    assert lct.test_key == "test_value"


def test_dictionary_style_access() -> None:
    lct = LayeredConfigTree()
    lct.update({"test_key": "test_value", "test_key2": "test_value2"})

    assert lct["test_key"] == "test_value"
    assert lct["test_key2"] == "test_value2"

    with pytest.raises(DuplicatedConfigurationError):
        lct["test_key2"] = "test_value3"

    assert lct["test_key2"] == "test_value2"
    assert lct["test_key"] == "test_value"


def test_get_missing_key() -> None:
    lct = LayeredConfigTree()
    with pytest.raises(ConfigurationKeyError):
        _ = lct.missing_key


def test_set_missing_key() -> None:
    lct = LayeredConfigTree()
    with pytest.raises(ConfigurationKeyError):
        lct.missing_key = "test_value"
    with pytest.raises(ConfigurationKeyError):
        lct["missing_key"] = "test_value"


def test_multiple_layer_get() -> None:
    lct = LayeredConfigTree(layers=["first", "second", "third"])
    lct._set_with_metadata("test_key", "test_with_source_value", "first", source=None)
    lct._set_with_metadata("test_key", "test_value2", "second", source=None)
    lct._set_with_metadata("test_key", "test_value3", "third", source=None)

    lct._set_with_metadata("test_key2", "test_value4", "first", source=None)
    lct._set_with_metadata("test_key2", "test_value5", "second", source=None)

    lct._set_with_metadata("test_key3", "test_value6", "first", source=None)

    assert lct.test_key == "test_value3"
    assert lct.test_key2 == "test_value5"
    assert lct.test_key3 == "test_value6"


def test_outer_layer_set() -> None:
    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct._set_with_metadata("test_key", "test_value", "inner", source=None)
    lct._set_with_metadata("test_key", "test_value3", layer=None, source=None)
    assert lct.test_key == "test_value3"
    assert lct["test_key"] == "test_value3"

    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct._set_with_metadata("test_key", "test_value", "inner", source=None)
    lct.test_key = "test_value3"
    assert lct.test_key == "test_value3"
    assert lct["test_key"] == "test_value3"

    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct._set_with_metadata("test_key", "test_value", "inner", source=None)
    lct["test_key"] = "test_value3"
    assert lct.test_key == "test_value3"
    assert lct["test_key"] == "test_value3"


def test_update_dict() -> None:
    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct.update({"test_key": "test_value", "test_key2": "test_value2"}, layer="inner")
    lct.update({"test_key": "test_value3"}, layer="outer")

    assert lct.test_key == "test_value3"
    assert lct.test_key2 == "test_value2"


def test_update_dict_nested() -> None:
    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct.update(
        {"test_container": {"test_key": "test_value", "test_key2": "test_value2"}},
        layer="inner",
    )
    with pytest.raises(DuplicatedConfigurationError):
        lct.update({"test_container": {"test_key": "test_value3"}}, layer="inner")

    assert lct.test_container.test_key == "test_value"
    assert lct.test_container.test_key2 == "test_value2"

    lct.update({"test_container": {"test_key2": "test_value4"}}, layer="outer")

    assert lct.test_container.test_key2 == "test_value4"


def test_source_metadata() -> None:
    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct.update({"test_key": "test_value"}, layer="inner", source="initial_load")
    lct.update({"test_key": "test_value2"}, layer="outer", source="update")

    assert lct.metadata("test_key") == [
        {"layer": "inner", "source": "initial_load", "value": "test_value"},
        {"layer": "outer", "source": "update", "value": "test_value2"},
    ]


def test_exception_on_source_for_missing_key() -> None:
    lct = LayeredConfigTree(layers=["inner", "outer"])
    lct.update({"test_key": "test_value"}, layer="inner", source="initial_load")

    with pytest.raises(ConfigurationKeyError):
        lct.metadata("missing_key")


def test_unused_keys() -> None:
    lct = LayeredConfigTree(
        {"test_key": {"test_key2": "test_value", "test_key3": "test_value2"}}
    )

    assert lct.unused_keys() == ["test_key.test_key2", "test_key.test_key3"]
    _ = lct.test_key.test_key2

    assert lct.unused_keys() == ["test_key.test_key3"]

    _ = lct.test_key.test_key3

    assert not lct.unused_keys()


def test_to_dict_dict() -> None:
    test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
    lct = LayeredConfigTree(test_dict)
    assert lct.to_dict() == test_dict


def test_to_dict_yaml(test_spec: Path) -> None:
    lct = LayeredConfigTree(str(test_spec))
    with test_spec.open() as f:
        yaml_config = yaml.full_load(f)
    assert yaml_config == lct.to_dict()


@pytest.mark.parametrize(
    "key, default_value, expected_value",
    [
        ("outer_layer_1", None, "test_value"),
        ("outer_layer_1", "some_default", "test_value"),
        ("fake_key", 0, 0),
        ("fake_key", "some_default", "some_default"),
    ],
)
def test_getter_single_values(
    key: str, default_value: str, expected_value: str, getter_dict: dict[str, Any]
) -> None:
    lct = LayeredConfigTree(getter_dict)

    if default_value is None:
        assert lct.get(key) == expected_value
    else:
        assert lct.get(key, default_value) == expected_value


def test_getter_chained(getter_dict: dict[str, Any]) -> None:
    lct = LayeredConfigTree(getter_dict)

    outer_layer_2 = lct.get("outer_layer_2")
    assert isinstance(outer_layer_2, LayeredConfigTree)
    assert outer_layer_2.to_dict() == getter_dict["outer_layer_2"]
    assert outer_layer_2.get("inner_layer") == "test_value2"


def test_getter_default_values(getter_dict: dict[str, Any]) -> None:
    lct = LayeredConfigTree(getter_dict)

    assert lct.get("fake_key") is None

    default_value = lct.get("fake_key", {})
    # checking default_value equals {} is not enough for mypy to know it's a dict
    assert default_value == {} and isinstance(default_value, dict)
    assert default_value.get("another_fake_key") is None


def test_tree_getter(getter_dict: dict[str, Any]) -> None:
    lct = LayeredConfigTree(getter_dict)

    assert lct.get_tree("outer_layer_2").to_dict() == getter_dict["outer_layer_2"]
    with pytest.raises(ConfigurationError, match="must return a LayeredConfigTree"):
        lct.get_tree("outer_layer_1")
    with pytest.raises(ConfigurationError, match="No value at name"):
        lct.get_tree("fake_key")


def test_equals() -> None:
    # TODO: Assert should succeed, instead of raising, once equality is
    # implemented for LayeredConfigTrees
    with pytest.raises(NotImplementedError):
        test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
        lct = LayeredConfigTree(test_dict)
        lct2 = LayeredConfigTree(test_dict.copy())
        assert lct == lct2


def test_to_from_pickle() -> None:
    test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
    second_layer = {"configuration": {"time": {"start": {"year": 2001}}}}
    lct = LayeredConfigTree(test_dict, layers=["first_layer", "second_layer"])
    lct.update(second_layer, layer="second_layer")
    unpickled = pickle.loads(pickle.dumps(lct))

    # We can't just assert unpickled == config because
    # equals doesn't work with our custom attribute
    # accessor scheme (also why pickling didn't use to work).
    # See the previous xfailed test.
    assert unpickled.to_dict() == lct.to_dict()
    assert unpickled._frozen == lct._frozen
    assert unpickled._name == lct._name
    assert unpickled._layers == lct._layers


def test_freeze() -> None:
    lct = LayeredConfigTree(data={"configuration": {"time": {"start": {"year": 2000}}}})
    lct.freeze()

    with pytest.raises(ConfigurationError):
        lct.update(data={"configuration": {"time": {"end": {"year": 2001}}}})


def test_retrieval_behavior() -> None:
    layer_inner = "inner"
    layer_middle = "middle"
    layer_outer = "outer"

    default_cfg_value = "value_a"

    layer_list = [layer_inner, layer_middle, layer_outer]
    # update the LayeredConfigTree layers in different order and verify that has no effect on
    #  the values retrieved ("outer" is retrieved when no layer is specified regardless of
    #  the initialization order
    for scenario in [layer_list, list(reversed(layer_list))]:
        lct = LayeredConfigTree(layers=layer_list)
        for layer in scenario:
            lct.update({default_cfg_value: layer}, layer=layer)
        assert lct.get_from_layer(default_cfg_value) == layer_outer
        assert lct.get_from_layer(default_cfg_value, layer=layer_outer) == layer_outer
        assert lct.get_from_layer(default_cfg_value, layer=layer_middle) == layer_middle
        assert lct.get_from_layer(default_cfg_value, layer=layer_inner) == layer_inner


def test_repr_display() -> None:
    expected_repr = """\
    Key1:
        override_2: value_ov_2
            source: ov2_src
        override_1: value_ov_1
            source: ov1_src
        base: value_base
            source: base_src"""
    # codifies the notion that repr() displays values from most to least overridden
    #  regardless of initialization order
    layers = ["base", "override_1", "override_2"]
    lct = LayeredConfigTree(layers=layers)

    lct.update({"Key1": "value_ov_2"}, layer="override_2", source="ov2_src")
    lct.update({"Key1": "value_ov_1"}, layer="override_1", source="ov1_src")
    lct.update({"Key1": "value_base"}, layer="base", source="base_src")
    assert repr(lct) == textwrap.dedent(expected_repr)

    lct = LayeredConfigTree(layers=layers)
    lct.update({"Key1": "value_base"}, layer="base", source="base_src")
    lct.update({"Key1": "value_ov_1"}, layer="override_1", source="ov1_src")
    lct.update({"Key1": "value_ov_2"}, layer="override_2", source="ov2_src")
    assert repr(lct) == textwrap.dedent(expected_repr)
