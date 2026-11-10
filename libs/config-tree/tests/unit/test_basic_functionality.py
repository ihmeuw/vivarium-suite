import pickle
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

from vivarium.config_tree import (
    ConfigNode,
    ConfigTree,
    ConfigurationError,
    ConfigurationKeyError,
    DuplicatedConfigurationError,
    ImproperAccessError,
    MissingLayerError,
    load_yaml,
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
def empty_tree(layers: list[str]) -> ConfigTree:
    return ConfigTree(layers=layers)


@pytest.fixture
def nested_dict() -> dict[str, Any]:
    return {
        "outer_layer_1": "test_value",
        "outer_layer_2": {"inner_layer": "test_value2"},
        "outer_layer_3": {"inner_layer_1": {"inner_layer_2": "test_value3"}},
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
    with pytest.raises(
        ConfigurationKeyError, match=f"No value stored in this ConfigNode {empty_node.name}."
    ):
        empty_node._get_value_with_source(layer=None)

    for layer in empty_node._layers:
        with pytest.raises(
            MissingLayerError,
            match=f"No value stored in this ConfigNode {empty_node.name}.",
        ):
            empty_node._get_value_with_source(layer=layer)

    assert not empty_node.accessed


def test_node_get_value_with_source_full_node_missing_layer(full_node: ConfigNode) -> None:
    with pytest.raises(
        MissingLayerError,
        match="No value stored in this ConfigNode test_node at layer non_existent_layer.",
    ):
        full_node._get_value_with_source(layer="non_existent_layer")


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
    full_node._accessed = False  # reset

    assert full_node.get_value(layer=None) == f"test_value_{len(full_node._layers)}"
    assert full_node.accessed
    full_node._accessed = False  # reset

    for i, layer in enumerate(full_node._layers):
        assert full_node.get_value(layer=layer) == f"test_value_{i + 1}"
        assert full_node.accessed
        full_node._accessed = False  # reset

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


def test_tree_creation(empty_tree: ConfigTree) -> None:
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
    assert ConfigTree._coerce(data, src) == (data, src)
    data = {"key": "val"}
    assert ConfigTree._coerce(data, src) == (data, src)
    data = {"key1": {"sub_key1": ["val", "val", "val"], "sub_key2": "val"}, "key2": "val"}
    assert ConfigTree._coerce(data, src) == (data, src)


def test_tree_coerce_str() -> None:
    src = "test"
    data = """\
    key: val"""
    assert ConfigTree._coerce(data, src) == ({"key": "val"}, src)
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
    assert ConfigTree._coerce(data, src) == (expected_dict, src)
    data = """\
        key1:
            sub_key1: [val, val, val]
            sub_key2: val
        key2: val"""
    expected_dict = {
        "key1": {"sub_key1": ["val", "val", "val"], "sub_key2": "val"},
        "key2": "val",
    }
    assert ConfigTree._coerce(data, src) == (expected_dict, src)


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
    assert ConfigTree._coerce(str(model_spec_path), src) == (expected_dict, src)
    assert ConfigTree._coerce(str(model_spec_path), None) == (
        expected_dict,
        str(model_spec_path),
    )


def test_single_layer() -> None:
    tree = ConfigTree()
    tree.update({"test_key": "test_value", "test_key2": "test_value2"})

    assert tree.test_key == "test_value"
    assert tree.test_key2 == "test_value2"

    with pytest.raises(DuplicatedConfigurationError):
        tree.test_key2 = "test_value3"

    assert tree.test_key2 == "test_value2"
    assert tree.test_key == "test_value"


def test_dictionary_style_access() -> None:
    tree = ConfigTree()
    tree.update({"test_key": "test_value", "test_key2": "test_value2"})

    assert tree["test_key"] == "test_value"
    assert tree["test_key2"] == "test_value2"

    with pytest.raises(DuplicatedConfigurationError):
        tree["test_key2"] = "test_value3"

    assert tree["test_key2"] == "test_value2"
    assert tree["test_key"] == "test_value"


def test_dunder_key_attr_style_access() -> None:
    tree = ConfigTree({"__dunder_key__": "val"}, layers=["layer1", "layer2"])
    # tree.update({"__dunder_key__": "val"})
    assert tree["__dunder_key__"] == "val"

    with pytest.raises(
        ImproperAccessError,
        match=re.escape(
            "Cannot get an attribute starting and ending with '__' via attribute "
            "access (i.e. dot notation). Use dictionary access instead "
            "(i.e. bracket notation)."
        ),
    ):
        tree.__dunder_key__

    with pytest.raises(
        ImproperAccessError,
        match=re.escape(
            "Cannot set an attribute starting and ending with '__' via attribute "
            "access (i.e. dot notation). Use dictionary access instead "
            "(i.e. bracket notation)."
        ),
    ):
        tree.__dunder_key__ = "val2"
    assert tree["__dunder_key__"] == "val"

    # check that we can modify the value in a new layer
    tree["__dunder_key__"] = "val2"
    assert tree["__dunder_key__"] == "val2"

    with pytest.raises(AttributeError):
        tree.__non_existent_dunder_key__


def test_get_missing_key_attr_style_access() -> None:
    tree = ConfigTree()
    with pytest.raises(ConfigurationKeyError, match="No value at name missing_key"):
        tree.missing_key


def test_get_missing_key_dict_style_access() -> None:
    tree = ConfigTree()
    with pytest.raises(ConfigurationKeyError, match="No value at name missing_key"):
        tree["missing_key"]


def test_set_missing_key() -> None:
    tree = ConfigTree()
    error_msg = re.escape(
        "New configuration keys can only be created with the update method."
    )
    with pytest.raises(ConfigurationKeyError, match=error_msg):
        tree.missing_key = "test_value"
    with pytest.raises(ConfigurationKeyError, match=error_msg):
        tree["missing_key"] = "test_value"


def test_multiple_layer_get() -> None:
    tree = ConfigTree(layers=["first", "second", "third"])
    tree._set_with_metadata("test_key", "test_with_source_value", "first", source=None)
    tree._set_with_metadata("test_key", "test_value2", "second", source=None)
    tree._set_with_metadata("test_key", "test_value3", "third", source=None)

    tree._set_with_metadata("test_key2", "test_value4", "first", source=None)
    tree._set_with_metadata("test_key2", "test_value5", "second", source=None)

    tree._set_with_metadata("test_key3", "test_value6", "first", source=None)

    assert tree.test_key == "test_value3"
    assert tree.test_key2 == "test_value5"
    assert tree.test_key3 == "test_value6"


def test_outer_layer_set() -> None:
    tree = ConfigTree(layers=["inner", "outer"])
    tree._set_with_metadata("test_key", "test_value", "inner", source=None)
    tree._set_with_metadata("test_key", "test_value3", layer=None, source=None)
    assert tree.test_key == "test_value3"
    assert tree["test_key"] == "test_value3"

    tree = ConfigTree(layers=["inner", "outer"])
    tree._set_with_metadata("test_key", "test_value", "inner", source=None)
    tree.test_key = "test_value3"
    assert tree.test_key == "test_value3"
    assert tree["test_key"] == "test_value3"

    tree = ConfigTree(layers=["inner", "outer"])
    tree._set_with_metadata("test_key", "test_value", "inner", source=None)
    tree["test_key"] = "test_value3"
    assert tree.test_key == "test_value3"
    assert tree["test_key"] == "test_value3"


def test_update_dict() -> None:
    tree = ConfigTree(layers=["inner", "outer"])
    tree.update({"test_key": "test_value", "test_key2": "test_value2"}, layer="inner")
    tree.update({"test_key": "test_value3"}, layer="outer")

    assert tree.test_key == "test_value3"
    assert tree.test_key2 == "test_value2"


def test_update_dict_nested() -> None:
    tree = ConfigTree(layers=["inner", "outer"])
    tree.update(
        {"test_container": {"test_key": "test_value", "test_key2": "test_value2"}},
        layer="inner",
    )
    with pytest.raises(DuplicatedConfigurationError):
        tree.update({"test_container": {"test_key": "test_value3"}}, layer="inner")

    assert tree.test_container.test_key == "test_value"
    assert tree.test_container.test_key2 == "test_value2"

    tree.update({"test_container": {"test_key2": "test_value4"}}, layer="outer")

    assert tree.test_container.test_key2 == "test_value4"


def test_source_metadata() -> None:
    tree = ConfigTree(layers=["inner", "outer"])
    tree.update({"test_key": "test_value"}, layer="inner", source="initial_load")
    tree.update({"test_key": "test_value2"}, layer="outer", source="update")

    assert tree.metadata("test_key") == [
        {"layer": "inner", "source": "initial_load", "value": "test_value"},
        {"layer": "outer", "source": "update", "value": "test_value2"},
    ]


def test_exception_on_source_for_missing_key() -> None:
    tree = ConfigTree(layers=["inner", "outer"])
    tree.update({"test_key": "test_value"}, layer="inner", source="initial_load")

    with pytest.raises(ConfigurationKeyError):
        tree.metadata("missing_key")


def test_unused_keys() -> None:
    tree = ConfigTree({"test_key": {"test_key2": "test_value", "test_key3": "test_value2"}})

    assert tree.unused_keys() == ["test_key.test_key2", "test_key.test_key3"]
    _ = tree.test_key.test_key2

    assert tree.unused_keys() == ["test_key.test_key3"]

    _ = tree.test_key.test_key3

    assert not tree.unused_keys()


def test_to_dict_dict() -> None:
    test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
    tree = ConfigTree(test_dict)
    assert tree.to_dict() == test_dict


def test_to_dict_yaml(test_spec: Path) -> None:
    tree = ConfigTree(str(test_spec))
    yaml_config = load_yaml(test_spec)
    assert yaml_config == tree.to_dict()


class TestGet:
    """Tests for ``ConfigTree.get``."""

    @pytest.mark.parametrize(
        "key, default_value, expected_value",
        [
            ("outer_layer_1", None, "test_value"),
            ("outer_layer_1", "some_default", "test_value"),
            ("fake_key", 0, 0),
            ("fake_key", "some_default", "some_default"),
        ],
    )
    def test_single_values(
        self, key: str, default_value: str, expected_value: str, nested_dict: dict[str, Any]
    ) -> None:
        tree = ConfigTree(nested_dict)

        if default_value is None:
            assert tree.get(key) == expected_value
        else:
            assert tree.get(key, default_value) == expected_value

    def test_chained_tree(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        assert (
            tree.get("outer_layer_3").get("inner_layer_1").to_dict()
            == tree.get(["outer_layer_3", "inner_layer_1"]).to_dict()
            == nested_dict["outer_layer_3"]["inner_layer_1"]
        )

    def test_chained_value(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        assert (
            tree.get("outer_layer_3").get("inner_layer_1").get("inner_layer_2")
            == tree.get(["outer_layer_3", "inner_layer_1", "inner_layer_2"])
            == nested_dict["outer_layer_3"]["inner_layer_1"]["inner_layer_2"]
        )

    def test_chained_default(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        assert tree.get(["outer_layer_3", "missing_key"], "foo") == "foo"

    @pytest.mark.parametrize(
        "keys",
        [
            # A key before the final one is missing
            ["fake_key", "inner_layer"],
            ["outer_layer_3", "fake_key", "inner_layer_2"],
            ["outer_layer_3", "inner_layer_1", "fake_key", "deeper"],
            # A key in the path resolves to a value rather than a sub-tree
            ["outer_layer_1", "deeper"],
            ["outer_layer_1", "deeper", "even_deeper"],
            ["outer_layer_2", "inner_layer", "deeper"],
            ["outer_layer_3", "inner_layer_1", "inner_layer_2", "deeper"],
        ],
    )
    def test_unresolvable_path_returns_default(
        self, keys: list[str], nested_dict: dict[str, Any]
    ) -> None:
        """``get`` returns the default for any key path that does not resolve."""
        tree = ConfigTree(nested_dict)
        assert tree.get(keys) is None
        assert tree.get(keys, "some_default") == "some_default"
        assert tree.get(keys, default_value=7) == 7

    def test_does_not_mutate_keys(self, nested_dict: dict[str, Any]) -> None:
        """``get`` leaves the caller's key list unchanged, whether or not the path resolves."""
        tree = ConfigTree(nested_dict)

        unresolvable = ["fake_key", "inner_layer"]
        assert tree.get(unresolvable) is None
        assert unresolvable == ["fake_key", "inner_layer"]

        prefix_is_value = ["outer_layer_1", "deeper"]
        assert tree.get(prefix_is_value) is None
        assert prefix_is_value == ["outer_layer_1", "deeper"]

        to_value = ["outer_layer_3", "inner_layer_1", "inner_layer_2"]
        assert tree.get(to_value) == "test_value3"
        assert to_value == ["outer_layer_3", "inner_layer_1", "inner_layer_2"]
        # A key path is reusable across calls
        assert tree.get(to_value) == "test_value3"

        to_tree = ["outer_layer_3", "inner_layer_1"]
        assert tree.get(to_tree).to_dict() == nested_dict["outer_layer_3"]["inner_layer_1"]
        assert to_tree == ["outer_layer_3", "inner_layer_1"]

    def test_chained_missing_layer_raises(self, nested_dict: dict[str, Any]) -> None:
        """A resolvable key path missing the requested layer still raises ``MissingLayerError``."""
        tree = ConfigTree(nested_dict, layers=["base", "override"])

        error_msg = re.escape(
            "No value stored in this ConfigNode outer_layer_2 at layer override."
        )
        with pytest.raises(MissingLayerError, match=error_msg):
            tree.get(["outer_layer_2", "inner_layer"], layer="override")

        # Leniency covers missing keys only, so a default does not suppress the error
        with pytest.raises(MissingLayerError, match=error_msg):
            tree.get(["outer_layer_2", "inner_layer"], "some_default", layer="override")

    @pytest.mark.parametrize("layer", ["base", "this-layer-does-not-exist"])
    def test_subtree_retrieved_when_layer_does_not_exist(
        self, layer: str, nested_dict: dict[str, Any]
    ) -> None:
        """A key path resolving to a sub-tree returns it and ignores ``layer`` entirely."""
        tree = ConfigTree(nested_dict, layers=["base", "override"])
        assert (
            tree.get(["outer_layer_3", "inner_layer_1"], layer=layer).to_dict()
            == nested_dict["outer_layer_3"]["inner_layer_1"]
        )

    @pytest.mark.parametrize("layer", ["base", "override", "this-layer-does-not-exist"])
    @pytest.mark.parametrize(
        "keys",
        [
            ["fake_key"],
            ["fake_key", "inner_layer"],
            ["outer_layer_3", "fake_key", "inner_layer_2"],
            ["outer_layer_1", "deeper"],
            ["outer_layer_3", "inner_layer_1", "inner_layer_2", "deeper"],
        ],
    )
    def test_unresolved_path_with_layer_returns_default(
        self, keys: list[str], layer: str, nested_dict: dict[str, Any]
    ) -> None:
        """An unresolvable key path returns the default even when a ``layer`` is requested."""
        tree = ConfigTree(nested_dict, layers=["base", "override"])
        assert tree.get(keys, layer=layer) is None
        assert tree.get(keys, "some_default", layer=layer) == "some_default"
        assert tree.get(keys, default_value=7, layer=layer) == 7

    def test_empty_keys_raises(self, nested_dict: dict[str, Any]) -> None:
        """``get`` and ``get_tree`` both raise an ``IndexError`` for an empty key path."""
        tree = ConfigTree(nested_dict)
        error_msg = re.escape("The 'keys' parameter must not be empty.")
        with pytest.raises(ValueError, match=error_msg):
            tree.get([])
        with pytest.raises(ValueError, match=error_msg):
            tree.get_tree([])

    def test_bad_keys_type_raises(self) -> None:
        """``get`` raises a ``TypeError`` if ``keys`` is neither a string nor a list."""
        tree = ConfigTree({"outer_layer": {"inner_layer": "test_value"}})
        error_msg = re.escape("The 'keys' parameter must be a string or a list of strings.")
        bad_keys: list[Any] = [1, ("outer_layer", "inner_layer")]
        for keys in bad_keys:
            with pytest.raises(TypeError, match=error_msg):
                tree.get(keys)
            with pytest.raises(TypeError, match=error_msg):
                tree.get(keys, "some_default")

    def test_defaults_and_layers(self) -> None:
        tree = ConfigTree(layers=["base", "override"])
        tree.update({"outer": {"inner": "base-value"}}, layer="base")
        tree.update({"outer": {"new-inner": "new-value"}}, layer="override")
        assert (
            tree.get(["outer", "new-inner"])
            == tree.get(["outer", "new-inner"], layer="override")
            == "new-value"
        )

    def test_missing_layer_raises(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict, layers=["base"], name="test_tree")
        with pytest.raises(
            MissingLayerError,
            match="No value stored in this ConfigNode test_tree at layer this-layer-does-not-exist.",
        ):
            tree.get("outer_layer_1", layer="this-layer-does-not-exist")


class TestGetTree:
    """Tests for ``ConfigTree.get_tree``."""

    def test_single_key(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        assert tree.get_tree("outer_layer_2").to_dict() == nested_dict["outer_layer_2"]

    def test_returns_value_raises(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        with pytest.raises(ConfigurationError, match="must return a ConfigTree"):
            tree.get_tree("outer_layer_1")

    def test_missing_key_raises(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        with pytest.raises(
            ConfigurationError, match=re.escape("No value at key mapping '['fake_key']'.")
        ):
            tree.get_tree("fake_key")

    def test_chained(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        assert (
            tree.get_tree("outer_layer_3").get_tree("inner_layer_1").to_dict()
            == tree.get_tree(["outer_layer_3", "inner_layer_1"]).to_dict()
            == nested_dict["outer_layer_3"]["inner_layer_1"]
        )

    def test_chained_returns_value_raises(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        with pytest.raises(ConfigurationError, match="get_tree must return a ConfigTree"):
            tree.get_tree(["outer_layer_3", "inner_layer_1", "inner_layer_2"])

    def test_chained_missing_key_raises(self, nested_dict: dict[str, Any]) -> None:
        tree = ConfigTree(nested_dict)
        with pytest.raises(
            ConfigurationKeyError,
            match=re.escape("No value at key mapping '['outer_layer_3', 'whoops']'."),
        ):
            tree.get_tree(["outer_layer_3", "whoops"])


def test_equals() -> None:
    # TODO: Assert should succeed, instead of raising, once equality is
    # implemented for ConfigTrees
    with pytest.raises(NotImplementedError):
        test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
        tree = ConfigTree(test_dict)
        tree2 = ConfigTree(test_dict.copy())
        assert tree == tree2


def test_to_from_pickle() -> None:
    test_dict = {"configuration": {"time": {"start": {"year": 2000}}}}
    second_layer = {"configuration": {"time": {"start": {"year": 2001}}}}
    tree = ConfigTree(test_dict, layers=["first_layer", "second_layer"])
    tree.update(second_layer, layer="second_layer")
    unpickled = pickle.loads(pickle.dumps(tree))

    # We can't just assert unpickled == config because
    # equals doesn't work with our custom attribute
    # accessor scheme (also why pickling didn't use to work).
    # See the previous xfailed test.
    assert unpickled.to_dict() == tree.to_dict()
    assert unpickled._frozen == tree._frozen
    assert unpickled._name == tree._name
    assert unpickled._layers == tree._layers


def test_freeze() -> None:
    tree = ConfigTree(data={"configuration": {"time": {"start": {"year": 2000}}}})
    tree.freeze()

    with pytest.raises(ConfigurationError):
        tree.update(data={"configuration": {"time": {"end": {"year": 2001}}}})


def test_retrieval_from_layer() -> None:
    layer_inner = "inner"
    layer_middle = "middle"
    layer_outer = "outer"

    default_cfg_value = "value_a"

    layer_list = [layer_inner, layer_middle, layer_outer]
    # update the ConfigTree layers in different order and verify that has no effect on
    #  the values retrieved ("outer" is retrieved when no layer is specified regardless of
    #  the initialization order
    for scenario in [layer_list, list(reversed(layer_list))]:
        tree = ConfigTree(layers=layer_list)
        for layer in scenario:
            tree.update({default_cfg_value: layer}, layer=layer)
        assert tree.get(default_cfg_value) == layer_outer
        assert tree.get(default_cfg_value, layer=layer_outer) == layer_outer
        assert tree.get(default_cfg_value, layer=layer_middle) == layer_middle
        assert tree.get(default_cfg_value, layer=layer_inner) == layer_inner


@pytest.fixture()
def nested_tree() -> ConfigTree:
    tree = ConfigTree(layers=["base", "override"])
    tree.update({"outer": {"inner": {"one": 1, "two": 2}}}, layer="base")
    tree.update(
        {"outer": {"inner": {"one": 100, "two": 200}, "new": "foo"}}, layer="override"
    )
    return tree


def test_nested_retrieval_default_layer(nested_tree: ConfigTree) -> None:
    assert nested_tree.get(["outer", "inner", "one"]) == 100
    assert nested_tree.get(["outer", "inner", "two"]) == 200
    assert nested_tree.get(["outer", "new"]) == "foo"


def test_nested_retrieval_from_layer(nested_tree: ConfigTree) -> None:
    # override layer
    assert nested_tree.get(["outer", "inner", "one"], layer="override") == 100
    assert nested_tree.get(["outer", "inner", "two"], layer="override") == 200
    assert nested_tree.get(["outer", "new"], layer="override") == "foo"
    # base layer
    assert nested_tree.get(["outer", "inner", "one"], layer="base") == 1
    assert nested_tree.get(["outer", "inner", "two"], layer="base") == 2


def test_nested_retrieval_missing_key_returns_default(
    nested_tree: ConfigTree,
) -> None:
    assert (
        nested_tree.get(["outer", "oops"], "missing-from-override-layer")
        == nested_tree.get(["outer", "oops"], "missing-from-override-layer", layer="override")
        == nested_tree.get(["outer", "oops"], "missing-from-override-layer", layer="base")
        == "missing-from-override-layer"
    )


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
    tree = ConfigTree(layers=layers)

    tree.update({"Key1": "value_ov_2"}, layer="override_2", source="ov2_src")
    tree.update({"Key1": "value_ov_1"}, layer="override_1", source="ov1_src")
    tree.update({"Key1": "value_base"}, layer="base", source="base_src")
    assert repr(tree) == textwrap.dedent(expected_repr)

    tree = ConfigTree(layers=layers)
    tree.update({"Key1": "value_base"}, layer="base", source="base_src")
    tree.update({"Key1": "value_ov_1"}, layer="override_1", source="ov1_src")
    tree.update({"Key1": "value_ov_2"}, layer="override_2", source="ov2_src")
    assert repr(tree) == textwrap.dedent(expected_repr)
