from pathlib import Path

from layered_config_tree import LayeredConfigTree

TEST_YAML_ONE = """
test_section:
    test_key: test_value
    test_key2: test_value2
test_section2:
    test_key: test_value3
    test_key2: test_value4
"""


def test_load_yaml_string() -> None:
    lct = LayeredConfigTree()
    lct.update(TEST_YAML_ONE, source="inline_test")

    assert lct.test_section.test_key == "test_value"
    assert lct.test_section.test_key2 == "test_value2"
    assert lct.test_section2.test_key == "test_value3"


def test_load_yaml_file(tmp_path: Path) -> None:
    tmp_file = tmp_path / "test_file.yaml"
    tmp_file.write_text(TEST_YAML_ONE)

    lct = LayeredConfigTree()
    lct.update(str(tmp_file))

    assert lct.test_section.test_key == "test_value"
    assert lct.test_section.test_key2 == "test_value2"
    assert lct.test_section2.test_key == "test_value3"
