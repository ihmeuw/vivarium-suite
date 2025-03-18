import re
from pathlib import Path

import pytest

from layered_config_tree import DuplicatedConfigurationError, LayeredConfigTree

TEST_YAML_ONE = """
test_section:
    test_key: test_value
    test_key2: test_value2
test_section2:
    test_key: test_value3
    test_key2: test_value4
"""

TEST_YAML_DUPLICATE_KEYS = """
cats:
    simba:
        size: tiny
        features:
            - cute
            - playful
        color: brown
    garfield:
        size: chonky
        traits:
            - lazy
            - grumpy
            - loves lasagna
        color: orange
        size: thick  # first duplicate; we raise here
        color: brown  # second duplicate; no raise

"""


def test_load_yaml_string() -> None:
    lct = LayeredConfigTree()
    lct.update(TEST_YAML_ONE, source="inline_test")

    assert lct.test_section.test_key == "test_value"
    assert lct.test_section.test_key2 == "test_value2"
    assert lct.test_section2.test_key == "test_value3"


@pytest.mark.parametrize("path_type", [str, Path])
def test_load_yaml_file(tmp_path: Path, path_type: type[str | Path]) -> None:
    tmp_file = tmp_path / "test_file.yaml"
    tmp_file.write_text(TEST_YAML_ONE)

    lct = LayeredConfigTree()
    filepath_to_test = str(tmp_file) if path_type is str else tmp_file
    lct.update(filepath_to_test)

    assert lct.test_section.test_key == "test_value"
    assert lct.test_section.test_key2 == "test_value2"
    assert lct.test_section2.test_key == "test_value3"


@pytest.mark.parametrize("duplicates", [True, False])
@pytest.mark.parametrize("load_from_file", [True, False])
def test_load_yaml_duplicates_raise(
    duplicates: bool, load_from_file: bool, tmp_path: Path
) -> None:
    test_str: str = TEST_YAML_DUPLICATE_KEYS if duplicates else TEST_YAML_ONE
    if load_from_file:
        tmp_file = tmp_path / "test_duplicate_keys.yaml"
        tmp_file.write_text(test_str)
    test_yaml = tmp_file if load_from_file else test_str

    lct = LayeredConfigTree()

    if duplicates:
        with pytest.raises(
            DuplicatedConfigurationError,
            match=re.escape(
                "Duplicate key detected at same level of YAML: size. Resolve duplicates and try again."
            ),
        ):
            lct.update(test_yaml)
    else:
        # Only duplicate keys on the same level are problematic!
        lct.update(test_yaml)
