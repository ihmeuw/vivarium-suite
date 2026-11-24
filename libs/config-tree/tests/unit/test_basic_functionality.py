import copy
import pickle
import re
import textwrap
from collections.abc import Callable
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


def _keys(nodes: list[ConfigNode]) -> list[str]:
    return [".".join(node._get_key_path()) for node in nodes]


class _TextPrinter:
    """Collect what ``_repr_pretty_`` prints, standing in for IPython's printer."""

    def __init__(self) -> None:
        self.output = ""

    def text(self, text: str) -> None:
        self.output += text


def _pretty(node: ConfigNode) -> str:
    printer = _TextPrinter()
    node._repr_pretty_(printer, cycle=False)
    return printer.output


class TestGetLeaves:
    """Tests for listing a tree's leaf nodes and the parents they keep."""

    @pytest.fixture
    def tree(self) -> ConfigTree:
        return ConfigTree(
            {
                "time": {"start": 2020, "step_size": 1},
                "risk.child_wasting": {"data_sources": {"exposure": "artifact.key"}},
                "seed": 7,
            }
        )

    def test_lists_every_leaf_in_the_order_added(self, tree: ConfigTree) -> None:
        assert [node._get_key_path() for node in tree.get_leaves()] == [
            ("time", "start"),
            ("time", "step_size"),
            ("risk.child_wasting", "data_sources", "exposure"),
            ("seed",),
        ]

    @pytest.mark.parametrize(
        "keys",
        [
            ["time", "start"],
            ["time", "step_size"],
            ["risk.child_wasting", "data_sources", "exposure"],
            ["seed"],
        ],
    )
    def test_returns_the_nodes_held_by_the_tree(
        self, tree: ConfigTree, keys: list[str]
    ) -> None:
        held = tree._get_node(keys)
        assert any(node is held for node in tree.get_leaves())

    def test_a_subtree_keeps_the_keys_above_it(self, tree: ConfigTree) -> None:
        [exposure] = tree.get_tree("risk.child_wasting").get_leaves()
        assert exposure._get_key_path() == ("risk.child_wasting", "data_sources", "exposure")

    def test_listing_does_not_mark_nodes_accessed(self, tree: ConfigTree) -> None:
        tree.get_leaves()
        assert len(tree.unused_keys()) == 4

    def test_an_empty_tree_has_no_leaves(self) -> None:
        assert ConfigTree().get_leaves() == []

    def test_a_frozen_tree_can_be_listed(self, tree: ConfigTree) -> None:
        tree.freeze()
        assert len(tree.get_leaves()) == 4

    @pytest.mark.parametrize(
        "copy_tree",
        [lambda tree: pickle.loads(pickle.dumps(tree)), copy.deepcopy],
        ids=["pickle", "deepcopy"],
    )
    def test_a_copied_tree_keeps_its_parents(
        self, tree: ConfigTree, copy_tree: Callable[[ConfigTree], ConfigTree]
    ) -> None:
        copied = copy_tree(tree)
        assert _keys(copied.get_leaves()) == _keys(tree.get_leaves())
        [step_size] = copied.find("time.step_size")
        assert step_size._parent is copied.time


class TestPrettyRepr:
    """Tests for how IPython displays a node."""

    @pytest.fixture
    def tree(self) -> ConfigTree:
        tree = ConfigTree(layers=["base", "override"])
        tree.update(
            {
                "configuration": {
                    "time": {"step_size": 1},
                    "risk.child_wasting": {"distribution_type": "ordered_polytomous"},
                },
                "seed": 7,
            },
            layer="base",
        )
        tree.update({"configuration": {"time": {"step_size": 10}}}, layer="override")
        return tree

    def test_a_node_displays_its_keys_and_outermost_value(self, tree: ConfigTree) -> None:
        assert [_pretty(node) for node in tree.get_leaves()] == [
            'ConfigNode(("configuration", "time", "step_size"), 10)',
            'ConfigNode(("configuration", "risk.child_wasting", "distribution_type"), '
            "'ordered_polytomous')",
            'ConfigNode(("seed",), 7)',
        ]

    def test_a_dotted_key_and_nested_keys_display_differently(self) -> None:
        tree = ConfigTree({"foo": {"bar": {"baz": 1}}, "foo.bar": {"baz": 10}})
        assert [_pretty(node) for node in tree.get_leaves()] == [
            'ConfigNode(("foo", "bar", "baz"), 1)',
            'ConfigNode(("foo.bar", "baz"), 10)',
        ]

    def test_a_key_with_a_quote_is_escaped(self) -> None:
        tree = ConfigTree({'say "hi"': 1})
        [node] = tree.get_leaves()
        assert _pretty(node) == 'ConfigNode(("say \\"hi\\"",), 1)'

    def test_displaying_does_not_mark_the_node_accessed(self, tree: ConfigTree) -> None:
        for node in tree.get_leaves():
            _pretty(node)
        assert len(tree.unused_keys()) == 3

    def test_a_node_with_no_value_displays_its_keys(self) -> None:
        """An update to a missing layer leaves an empty node behind, which find returns."""
        tree = ConfigTree(layers=["base"])
        with pytest.raises(ConfigurationKeyError):
            tree.update({"time": {"step_size": 1}}, layer="no_such_layer")
        [step_size] = tree.find("step_size")
        assert _pretty(step_size) == 'ConfigNode(("time", "step_size"))'

    def test_a_node_outside_a_tree_displays_as_its_repr(self) -> None:
        node = ConfigNode(["base"], name="test_node")
        node.update("test_value", layer="base", source="test")
        assert _pretty(node) == repr(node)


class TestUpdateFoundNode:
    """Tests for changing a value through a node found in the tree."""

    @pytest.fixture
    def tree(self) -> ConfigTree:
        tree = ConfigTree(layers=["base", "override"])
        tree.update({"time": {"step_size": 1}}, layer="base")
        return tree

    def test_the_tree_reads_the_new_value(self, tree: ConfigTree) -> None:
        [step_size] = tree.find("step_size")
        step_size.update(5)
        assert tree.time.step_size == 5

    def test_it_matches_assigning_through_the_tree(self, tree: ConfigTree) -> None:
        [step_size] = tree.find("step_size")
        step_size.update(5)

        assigned = ConfigTree(layers=["base", "override"])
        assigned.update({"time": {"step_size": 1}}, layer="base")
        assigned.time.step_size = 5

        assert step_size.metadata == assigned.time.metadata("step_size")

    def test_the_display_shows_the_new_value(self, tree: ConfigTree) -> None:
        [step_size] = tree.find("step_size")
        step_size.update(5)
        assert _pretty(step_size) == 'ConfigNode(("time", "step_size"), 5)'

    def test_a_second_update_at_the_outermost_layer_is_rejected(
        self, tree: ConfigTree
    ) -> None:
        [step_size] = tree.find("step_size")
        step_size.update(5)
        with pytest.raises(DuplicatedConfigurationError):
            step_size.update(6)


class TestFind:
    """Tests for finding leaf nodes by their keys."""

    @pytest.fixture
    def tree(self) -> ConfigTree:
        return ConfigTree(
            {
                "time": {"start": 2020, "end": 2025, "step_size": 1},
                "risk.child_wasting": {
                    "distribution_type": "ordered_polytomous",
                    "exposure_bounds": {"lower": 0.0, "upper": 1.0},
                },
                "population": {"population_size": 100},
            }
        )

    def test_returns_the_nodes_held_by_the_tree(self, tree: ConfigTree) -> None:
        [step_size] = tree.find("time.step_size")
        assert isinstance(step_size, ConfigNode)
        assert step_size is tree.time._children["step_size"]

    def test_only_leaves_are_returned(self, tree: ConfigTree) -> None:
        assert _keys(tree.find("exposure_bounds")) == [
            "risk.child_wasting.exposure_bounds.lower",
            "risk.child_wasting.exposure_bounds.upper",
        ]

    def test_a_fragment_need_not_be_the_whole_path(self, tree: ConfigTree) -> None:
        assert _keys(tree.find("wasting.dist")) == ["risk.child_wasting.distribution_type"]

    def test_matching_is_case_insensitive(self, tree: ConfigTree) -> None:
        assert _keys(tree.find("TIME.STEP_SIZE")) == ["time.step_size"]

    def test_the_pattern_is_literal_by_default(self, tree: ConfigTree) -> None:
        assert tree.find("risk.child.wasting") == []

    def test_a_pattern_is_a_regex_only_with_regex_true(self, tree: ConfigTree) -> None:
        pattern = r"^time\.(start|end)$"
        assert _keys(tree.find(pattern, regex=True)) == ["time.start", "time.end"]
        assert tree.find(pattern) == []

    def test_results_keep_the_order_keys_were_added(self, tree: ConfigTree) -> None:
        """The pattern skips time.start and time.end, so the matches are not contiguous."""
        assert _keys(tree.find("_")) == [
            "time.step_size",
            "risk.child_wasting.distribution_type",
            "risk.child_wasting.exposure_bounds.lower",
            "risk.child_wasting.exposure_bounds.upper",
            "population.population_size",
        ]

    def test_no_pattern_finds_every_leaf(self, tree: ConfigTree) -> None:
        assert tree.find() == tree.get_leaves()
        assert tree.find("") == tree.get_leaves()

    def test_no_match_returns_an_empty_list(self, tree: ConfigTree) -> None:
        assert tree.find("no_such_key") == []

    def test_an_invalid_regex_raises_a_useful_error(self, tree: ConfigTree) -> None:
        with pytest.raises(ValueError, match=re.escape("Invalid regular expression 'time['")):
            tree.find("time[", regex=True)

    def test_invalid_regex_text_is_harmless_without_the_flag(self, tree: ConfigTree) -> None:
        assert tree.find("time[") == []

    def test_finding_does_not_mark_nodes_accessed(self, tree: ConfigTree) -> None:
        unused = tree.unused_keys()
        tree.find()
        assert tree.unused_keys() == unused

    def test_a_subtree_matches_on_keys_from_the_root(self, tree: ConfigTree) -> None:
        """A subtree keeps its parent, so its nodes' keys start at the root."""
        assert _keys(tree.time.find(r"^time\.s", regex=True)) == [
            "time.start",
            "time.step_size",
        ]


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


class TestAbsolutePaths:
    """Tests for the ``absolute`` argument of ``ConfigTree.get`` and ``ConfigTree.get_tree``."""

    @staticmethod
    def _make_tree(layers: list[str] | None = None) -> ConfigTree:
        """Build a tree whose key names recur at different depths with different values."""
        data: dict[str, Any] = {
            "x": "root_x",
            "c": "root_c",
            "a": {
                "x": "a_x",
                "b": {"x": "a_b_x", "c": {"z": "a_b_c_z"}},
            },
            "b": {"x": "b_x"},
        }
        return ConfigTree(data, layers=layers if layers else ["base"])

    def test_default_is_relative(self) -> None:
        """``get`` and ``get_tree`` resolve from the called tree when ``absolute`` is omitted."""
        tree = self._make_tree()
        sub = tree.a

        assert sub.get("x") == sub.get("x", absolute=False) == "a_x"
        assert sub.get(["b", "x"]) == sub.get(["b", "x"], absolute=False) == "a_b_x"
        assert sub.get("b") is sub.get("b", absolute=False) is tree.a.b
        assert sub.get_tree("b") is sub.get_tree("b", absolute=False) is tree.a.b

    def test_get_value_from_subtree(self) -> None:
        """``get`` with ``absolute=True`` on a nested subtree returns the leaf value at the path from the root."""
        tree = self._make_tree()
        deep = tree.a.b

        assert deep.get("x", absolute=True) == "root_x"
        assert deep.get(["b", "x"], absolute=True) == "b_x"
        assert deep.get(["a", "x"], absolute=True) == "a_x"
        assert deep.get(["a", "b", "c", "z"], absolute=True) == "a_b_c_z"
        assert tree.a.get(["a", "b", "x"], absolute=True) == "a_b_x"

    def test_get_subtree_from_subtree(self) -> None:
        """``get`` with ``absolute=True`` on a nested subtree returns the subtree when the path ends at a tree."""
        tree = self._make_tree()
        deep = tree.a.b.c

        result = deep.get("b", absolute=True)
        assert isinstance(result, ConfigTree)
        assert result is tree.b
        assert deep.get("a", absolute=True) is tree.a
        assert deep.get(["a", "b"], absolute=True) is tree.a.b
        assert deep.get(["a", "b", "c"], absolute=True) is deep

    def test_get_tree_from_subtree(self) -> None:
        """``get_tree`` with ``absolute=True`` on a nested subtree returns the subtree at the path from the root."""
        tree = self._make_tree()
        deep = tree.a.b.c

        result = deep.get_tree("b", absolute=True)
        assert isinstance(result, ConfigTree)
        assert result is tree.b
        assert result.to_dict() == {"x": "b_x"}
        assert deep.get_tree("a", absolute=True) is tree.a
        assert deep.get_tree(["a", "b"], absolute=True) is tree.a.b
        assert deep.get_tree(["a", "b", "c"], absolute=True) is deep

    def test_root_absolute_matches_relative(self) -> None:
        """On the root tree, ``absolute=True`` and ``absolute=False`` return the same result."""
        tree = self._make_tree()

        value_paths: list[str | list[str]] = ["x", "c", ["a", "x"], ["a", "b", "c", "z"]]
        for keys in value_paths:
            assert tree.get(keys, absolute=True) == tree.get(keys, absolute=False)

        tree_paths: list[str | list[str]] = ["a", "b", ["a", "b"], ["a", "b", "c"]]
        for keys in tree_paths:
            assert tree.get(keys, absolute=True) is tree.get(keys, absolute=False)
            assert tree.get_tree(keys, absolute=True) is tree.get_tree(keys, absolute=False)

        missing_paths: list[str | list[str]] = ["missing", ["a", "missing"], ["x", "deeper"]]
        for keys in missing_paths:
            assert (
                tree.get(keys, "some_default", absolute=True)
                == tree.get(keys, "some_default", absolute=False)
                == "some_default"
            )

    def test_same_path_differs_relative_and_absolute(self) -> None:
        """A key path that resolves both relatively and absolutely from a subtree gives each result."""
        tree = self._make_tree()
        sub = tree.a
        deep = tree.a.b

        assert sub.get("x") == "a_x"
        assert sub.get("x", absolute=True) == "root_x"

        assert sub.get(["b", "x"]) == "a_b_x"
        assert sub.get(["b", "x"], absolute=True) == "b_x"

        assert sub.get("b") is tree.a.b
        assert sub.get("b", absolute=True) is tree.b
        assert sub.get_tree("b") is tree.a.b
        assert sub.get_tree("b", absolute=True) is tree.b

        assert deep.get("x") == "a_b_x"
        assert deep.get("x", absolute=True) == "root_x"

    def test_get_with_layer(self) -> None:
        """``get`` with ``absolute=True`` and ``layer`` returns the value at that layer."""
        tree = self._make_tree(layers=["base", "override"])
        tree.update({"x": "root_x_override"}, layer="override")
        deep = tree.a.b

        assert deep.get("x", layer="base", absolute=True) == "root_x"
        assert deep.get("x", layer="override", absolute=True) == "root_x_override"
        assert deep.get("x", absolute=True) == "root_x_override"
        # The relative leaf of the same name is unaffected by the root override
        assert deep.get("x", layer="base") == "a_b_x"

        # ["b", "x"] has no value at the override layer
        with pytest.raises(MissingLayerError):
            deep.get(["b", "x"], layer="override", absolute=True)

    def test_get_marks_leaf_accessed(self) -> None:
        """``get`` with ``absolute=True`` marks the leaf accessed, so the root's ``unused_keys`` omits it."""
        tree = self._make_tree()
        all_leaves = {"x", "c", "a.x", "a.b.x", "a.b.c.z", "b.x"}
        assert set(tree.unused_keys()) == all_leaves

        deep = tree.a.b
        assert deep.get(["b", "x"], absolute=True) == "b_x"

        # Only the absolutely-addressed leaf is marked, not the relative "b.x" under deep
        assert set(tree.unused_keys()) == all_leaves - {"b.x"}

        assert deep.get("x", absolute=True) == "root_x"
        assert set(tree.unused_keys()) == all_leaves - {"b.x", "x"}

    def test_subtrees_from_update_at_any_depth(self) -> None:
        """Subtrees that ``update`` creates at any depth resolve absolute key paths from the root."""
        tree = ConfigTree(layers=["base", "override"])
        tree.update({"x": "root_x"}, layer="base")
        tree.update({"a": {"b": {"c": {"x": "a_b_c_x"}}}}, layer="base")
        tree.a.b.update({"d": {"e": {"x": "a_b_d_e_x"}}}, layer="override")
        tree.update({"f": {"x": "f_x"}}, layer="override")

        subtrees = [tree.a, tree.a.b, tree.a.b.c, tree.a.b.d, tree.a.b.d.e, tree.f]
        for sub in subtrees:
            assert sub.get("x", absolute=True) == "root_x"
            assert sub.get(["a", "b", "d", "e", "x"], absolute=True) == "a_b_d_e_x"
            assert sub.get_tree("f", absolute=True) is tree.f

    def test_get_unresolved_returns_default(self) -> None:
        """``get`` with ``absolute=True`` returns ``default_value`` when only the relative path resolves."""
        tree = self._make_tree()
        sub = tree.a
        deep = tree.a.b

        # Relative paths resolve
        assert deep.get(["c", "z"]) == "a_b_c_z"
        assert sub.get(["b", "c", "z"]) == "a_b_c_z"
        assert sub.get(["b", "c"]) is tree.a.b.c

        # Absolute paths do not
        assert deep.get(["c", "z"], absolute=True) is None
        assert deep.get(["c", "z"], "some_default", absolute=True) == "some_default"
        assert sub.get(["b", "c", "z"], default_value=7, absolute=True) == 7
        assert sub.get(["b", "c"], "some_default", absolute=True) == "some_default"
        with_layer = sub.get(["b", "c"], "some_default", layer="base", absolute=True)
        assert with_layer == "some_default"

    def test_get_tree_unresolved_raises(self) -> None:
        """``get_tree`` with ``absolute=True`` raises ``ConfigurationKeyError`` when the absolute path does not resolve."""
        tree = self._make_tree()
        sub = tree.a
        deep = tree.a.b

        # Relative paths resolve
        assert sub.get_tree(["b", "c"]) is tree.a.b.c
        assert deep.get_tree("c") is tree.a.b.c

        with pytest.raises(ConfigurationKeyError):
            sub.get_tree(["b", "c"], absolute=True)
        with pytest.raises(ConfigurationKeyError):
            deep.get_tree("missing", absolute=True)
        # A key in the path resolves to a value rather than a sub-tree
        with pytest.raises(ConfigurationKeyError):
            deep.get_tree(["x", "deeper"], absolute=True)

    def test_get_tree_ends_at_value_raises(self) -> None:
        """``get_tree`` with ``absolute=True`` raises ``ConfigurationError`` when the absolute path ends at a value."""
        tree = self._make_tree()
        deep = tree.a.b

        # Relatively "c" is a tree; absolutely it is a value
        assert deep.get_tree("c") is tree.a.b.c

        with pytest.raises(ConfigurationError) as exc_info:
            deep.get_tree("c", absolute=True)
        assert not isinstance(exc_info.value, ConfigurationKeyError)

        with pytest.raises(ConfigurationError) as exc_info:
            deep.get_tree(["b", "x"], absolute=True)
        assert not isinstance(exc_info.value, ConfigurationKeyError)

    def test_empty_keys_raises(self) -> None:
        """``get`` and ``get_tree`` with ``absolute=True`` raise ``ValueError`` for an empty key path."""
        tree = self._make_tree()
        for t in [tree, tree.a, tree.a.b]:
            with pytest.raises(ValueError):
                t.get([], absolute=True)
            with pytest.raises(ValueError):
                t.get([], "some_default", absolute=True)
            with pytest.raises(ValueError):
                t.get_tree([], absolute=True)

    def test_pickle_round_trip(self) -> None:
        """A subtree of an unpickled tree resolves absolute key paths from the unpickled root."""
        tree = self._make_tree(layers=["base", "override"])
        unpickled = pickle.loads(pickle.dumps(tree))
        # Changes to the original after pickling must not be visible from the copy
        tree.update({"x": "changed"}, layer="override")
        tree.update({"new_key": "new_value"}, layer="base")

        deep = unpickled.a.b
        assert deep.get("x", absolute=True) == "root_x"
        assert deep.get("new_key", absolute=True) is None
        assert deep.get("b", absolute=True) is unpickled.b
        assert deep.get_tree("b", absolute=True) is unpickled.b
        assert deep.get_tree(["a", "b"], absolute=True) is deep
        assert deep.get_tree("b", absolute=True) is not tree.b

    def test_deepcopy(self) -> None:
        """A subtree of a deep-copied tree resolves absolute key paths from the copy's root, not the original."""
        tree = self._make_tree(layers=["base", "override"])
        copied = copy.deepcopy(tree)
        tree.update({"x": "changed"}, layer="override")
        tree.update({"new_key": "new_value"}, layer="base")

        deep = copied.a.b
        assert deep.get("x", absolute=True) == "root_x"
        assert deep.get("new_key", absolute=True) is None
        assert deep.get("b", absolute=True) is copied.b
        assert deep.get_tree("b", absolute=True) is copied.b
        assert deep.get_tree(["a", "b"], absolute=True) is deep
        assert deep.get_tree("b", absolute=True) is not tree.b

        # The original's subtrees still resolve from the original root
        assert tree.a.b.get("x", absolute=True) == "changed"
        assert tree.a.b.get_tree("b", absolute=True) is tree.b

    def test_deleted_subtree_is_own_root(self) -> None:
        """A subtree removed with ``del`` resolves absolute ``get`` and ``get_tree`` from itself, not its former root."""
        # Delete a nested subtree with bracket syntax
        tree = self._make_tree()
        deep = tree.a.b
        del tree.a["b"]

        assert deep.get("x", absolute=True) == "a_b_x"
        assert deep.get(["c", "z"], absolute=True) == "a_b_c_z"
        assert deep.c.get("x", absolute=True) == "a_b_x"
        assert deep.get_tree("c", absolute=True) is deep.c
        assert deep.c.get_tree("c", absolute=True) is deep.c
        # "b" and ["a", "x"] exist only in the former root
        assert deep.get("b", absolute=True) is None
        assert deep.get("b", "some_default", absolute=True) == "some_default"
        assert deep.get(["a", "x"], "some_default", absolute=True) == "some_default"
        with pytest.raises(ConfigurationKeyError):
            deep.get_tree("b", absolute=True)
        with pytest.raises(ConfigurationKeyError):
            deep.c.get_tree(["a", "b"], absolute=True)

        # Delete a top-level subtree with attribute syntax
        tree = self._make_tree()
        sub = tree.a
        del tree.a

        assert sub.get("x", absolute=True) == "a_x"
        assert sub.b.get("x", absolute=True) == "a_x"
        assert sub.b.c.get(["b", "x"], absolute=True) == "a_b_x"
        assert sub.b.get_tree("b", absolute=True) is sub.b
        assert sub.b.c.get_tree(["b", "c"], absolute=True) is sub.b.c
        assert sub.b.get_tree("b", absolute=True) is not tree.b
        # "c" exists only in the former root
        assert sub.b.get("c", "some_default", absolute=True) == "some_default"
        with pytest.raises(ConfigurationKeyError):
            sub.b.get_tree("c", absolute=True)
        with pytest.raises(ConfigurationKeyError):
            sub.b.c.get_tree(["a", "b"], absolute=True)

    def test_dotted_string_is_one_key(self) -> None:
        """With ``absolute=True``, a string that contains dots is one key, not a key path."""
        tree = ConfigTree(
            {"p.q": "literal", "p": {"q": "nested"}, "s": {"t": {"v": "s_t_v"}}}
        )
        deep = tree.s.t

        assert deep.get("p.q", absolute=True) == "literal"
        assert deep.get(["p.q"], absolute=True) == "literal"
        assert deep.get(["p", "q"], absolute=True) == "nested"

        # Without the literal key, the dotted string does not fall back to the nested path
        nested_only = ConfigTree({"p": {"q": "nested"}, "s": {"t": {"v": "s_t_v"}}})
        deep = nested_only.s.t
        assert deep.get("p.q", "some_default", absolute=True) == "some_default"
        assert deep.get(["p", "q"], absolute=True) == "nested"

    def test_tree_built_from_subtree_is_own_root(self) -> None:
        """A tree built from a subtree with ``ConfigTree(subtree)`` or ``update`` resolves absolute key paths from itself."""
        original = self._make_tree()
        sub = original.a

        constructed = ConfigTree(sub)
        assert constructed.get("x", absolute=True) == "a_x"
        assert constructed.b.get("x", absolute=True) == "a_x"
        assert constructed.b.c.get("x", absolute=True) == "a_x"
        assert constructed.b.get_tree("b", absolute=True) is constructed.b
        assert constructed.b.get_tree("b", absolute=True) is not original.b
        # "c" is a root key of the original only
        assert constructed.b.get("c", "some_default", absolute=True) == "some_default"

        other = ConfigTree({"y": "other_y"})
        other.update(sub)
        assert other.b.get("x", absolute=True) == "a_x"
        assert other.b.c.get("y", absolute=True) == "other_y"
        assert other.b.get_tree("b", absolute=True) is other.b
        assert other.b.get_tree("b", absolute=True) is not original.b
        assert other.b.get("c", "some_default", absolute=True) == "some_default"

        # The source subtree still resolves from its own root
        assert sub.get("x", absolute=True) == "root_x"
        assert sub.get_tree("b", absolute=True) is original.b


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


def _delete_by_attr(tree: ConfigTree, key: str) -> None:
    delattr(tree, key)


def _delete_by_item(tree: ConfigTree, key: str) -> None:
    del tree[key]


_DELETE_SYNTAXES = pytest.mark.parametrize(
    "delete",
    [_delete_by_attr, _delete_by_item],
    ids=["attribute", "bracket"],
)


class TestDeleteChild:
    """Deleting a child clears the child's link to its former parent."""

    def test_delattr_clears_subtree_parent(self) -> None:
        """``del tree.key`` on a ``ConfigTree`` child sets the child's ``_parent`` to None."""
        tree = ConfigTree({"x": 1, "a": {"b": {"y": 2}}})
        top = tree.a
        nested = tree.a.b

        del tree.a.b
        assert nested._parent is None
        assert "b" not in top

        del tree.a
        assert top._parent is None
        assert "a" not in tree

    def test_delitem_clears_subtree_parent(self) -> None:
        """``del tree["key"]`` on a ``ConfigTree`` child sets the child's ``_parent`` to None."""
        tree = ConfigTree({"x": 1, "a": {"b": {"y": 2}}})
        top = tree.a
        nested = tree.a.b

        del tree["a"]["b"]
        assert nested._parent is None
        assert "b" not in top

        del tree["a"]
        assert top._parent is None
        assert "a" not in tree

    def test_delattr_clears_node_parent(self) -> None:
        """``del tree.key`` on a ``ConfigNode`` child sets the node's ``_parent`` to None."""
        tree = ConfigTree({"x": 1, "a": {"y": 2}})
        x_node = tree._get_node(["x"])
        y_node = tree._get_node(["a", "y"])
        assert isinstance(x_node, ConfigNode) and isinstance(y_node, ConfigNode)

        del tree.x
        assert x_node._parent is None
        assert "x" not in tree

        del tree.a.y
        assert y_node._parent is None
        assert "y" not in tree.a

    def test_delitem_clears_node_parent(self) -> None:
        """``del tree["key"]`` on a ``ConfigNode`` child sets the node's ``_parent`` to None."""
        tree = ConfigTree({"x": 1, "a": {"y": 2}})
        x_node = tree._get_node(["x"])
        y_node = tree._get_node(["a", "y"])
        assert isinstance(x_node, ConfigNode) and isinstance(y_node, ConfigNode)

        del tree["x"]
        assert x_node._parent is None
        assert "x" not in tree

        del tree["a"]["y"]
        assert y_node._parent is None
        assert "y" not in tree["a"]

    @_DELETE_SYNTAXES
    def test_deleted_subtree_key_paths_start_from_itself(
        self, delete: Callable[[ConfigTree, str], None]
    ) -> None:
        """A deleted subtree's key path is empty, and the key paths of the trees and nodes inside it start from it."""
        tree = ConfigTree({"x": 1, "a": {"b": {"y": 2, "c": {"z": 3}}}})
        sub = tree.a.b
        inner = sub.c
        y_node = sub._get_node(["y"])
        z_node = sub._get_node(["c", "z"])
        assert isinstance(y_node, ConfigNode) and isinstance(z_node, ConfigNode)

        delete(tree.a, "b")

        assert sub._get_key_path() == ()
        assert inner._get_key_path() == ("c",)
        assert y_node._get_key_path() == ("y",)
        assert z_node._get_key_path() == ("c", "z")

    @_DELETE_SYNTAXES
    def test_deleted_subtree_find_and_display_start_from_itself(
        self, delete: Callable[[ConfigTree, str], None]
    ) -> None:
        """``find()`` on a deleted subtree and the IPython display of its nodes show key paths that start from the deleted subtree."""
        tree = ConfigTree({"x": 1, "a": {"b": {"y": 2, "c": {"z": 3}}}})
        sub = tree.a.b

        delete(tree.a, "b")

        assert [node._get_key_path() for node in sub.find()] == [("y",), ("c", "z")]
        [z_node] = sub.find("c.z")
        assert z_node._get_key_path() == ("c", "z")
        # The former parent keys are no longer part of the searched path
        assert sub.find("a.b") == []
        assert sub.find("a.b.c.z") == []

        assert [_pretty(node) for node in sub.find()] == [
            'ConfigNode(("y",), 2)',
            'ConfigNode(("c", "z"), 3)',
        ]

    @_DELETE_SYNTAXES
    def test_deleted_node_is_outside_any_tree(
        self, delete: Callable[[ConfigTree, str], None]
    ) -> None:
        """A deleted ``ConfigNode`` has an empty key path and displays as its ``repr``, the same as a node outside any tree."""
        tree = ConfigTree({"x": 1, "a": {"y": 2}})
        x_node = tree._get_node(["x"])
        y_node = tree._get_node(["a", "y"])
        assert isinstance(x_node, ConfigNode) and isinstance(y_node, ConfigNode)

        delete(tree, "x")
        delete(tree.a, "y")

        for node in [x_node, y_node]:
            assert node._get_key_path() == ()
            assert _pretty(node) == repr(node)

    @_DELETE_SYNTAXES
    def test_rest_of_tree_unchanged_after_delete(
        self, delete: Callable[[ConfigTree, str], None]
    ) -> None:
        """Deleting one child leaves the other children, their parent links, and their key paths unchanged."""
        tree = ConfigTree({"x": 1, "a": {"y": 2, "w": 4}, "b": {"z": 3}})
        a = tree.a
        assert isinstance(a, ConfigTree)
        b = tree.b
        x_node = tree._get_node(["x"])
        w_node = tree._get_node(["a", "w"])
        z_node = tree._get_node(["b", "z"])
        assert isinstance(x_node, ConfigNode)
        assert isinstance(w_node, ConfigNode)
        assert isinstance(z_node, ConfigNode)

        delete(tree.a, "y")
        delete(tree, "b")

        assert list(tree.keys()) == ["x", "a"]
        assert list(a.keys()) == ["w"]
        assert tree._get_node(["a"]) is a
        assert tree._get_node(["x"]) is x_node
        assert a._get_node(["w"]) is w_node

        assert a._parent is tree
        assert x_node._parent is tree
        assert w_node._parent is a
        # A child of the deleted subtree still links to that subtree
        assert z_node._parent is b

        assert a._get_key_path() == ("a",)
        assert x_node._get_key_path() == ("x",)
        assert w_node._get_key_path() == ("a", "w")

        assert tree.to_dict() == {"x": 1, "a": {"w": 4}}

    @_DELETE_SYNTAXES
    def test_delete_missing_key_does_nothing(
        self, delete: Callable[[ConfigTree, str], None]
    ) -> None:
        """Deleting a key that is not in the tree, with either syntax, does not raise and leaves the tree unchanged."""
        tree = ConfigTree({"x": 1, "a": {"y": 2}})
        a = tree.a
        assert isinstance(a, ConfigTree)
        x_node = tree._get_node(["x"])
        y_node = tree._get_node(["a", "y"])
        assert isinstance(x_node, ConfigNode) and isinstance(y_node, ConfigNode)
        before = tree.to_dict()

        delete(tree, "missing")
        delete(tree.a, "missing")

        assert tree.to_dict() == before
        assert list(tree.keys()) == ["x", "a"]
        assert tree._get_node(["a"]) is a
        assert a._parent is tree
        assert x_node._parent is tree
        assert y_node._parent is a

    def test_deleted_subtree_can_be_added_back_by_value(self) -> None:
        """A deleted subtree added back with ``update(subtree.to_dict())`` builds a new child that resolves from its new tree."""
        tree = ConfigTree({"x": "root_x", "a": {"b": {"y": "b_y"}}})
        sub = tree.a.b
        del tree.a["b"]

        tree.a.update({"b": sub.to_dict()})

        added = tree.a.b
        assert added is not sub
        assert added.to_dict() == {"y": "b_y"}
        assert added._parent is tree.a
        assert added._get_key_path() == ("a", "b")
        [y_node] = added.find("y")
        assert y_node._get_key_path() == ("a", "b", "y")
        assert added.get("x", absolute=True) == "root_x"
        assert added.get_tree("a", absolute=True) is tree.a

        # The deleted subtree stays detached
        assert sub._parent is None
        assert sub._get_key_path() == ()
        assert sub.get("x", absolute=True) is None
