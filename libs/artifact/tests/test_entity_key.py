import pytest

from vivarium.artifact.entity_key import EntityKey, is_entity_key


@pytest.mark.parametrize(
    "key", ["population.structure", "cause.diarrhea.incidence", "metadata.versions"]
)
def test_is_entity_key_valid(key: str) -> None:
    assert is_entity_key(key) is True


@pytest.mark.parametrize("key", ["hello", "a.b.c.d", "", ".", ".a", "a.", "a..c"])
def test_is_entity_key_invalid(key: str) -> None:
    assert is_entity_key(key) is False


def test_EntityKey_init_failure() -> None:
    bad_keys = ["hello", "a.b.c.d", "", ".", ".coconut", "a.", "a..c"]

    for k in bad_keys:
        error_msg = f'Invalid format for HDF key: {k}. Acceptable formats are "type.name.measure" and "type.measure"'
        with pytest.raises(ValueError, match=error_msg):
            EntityKey(k)


def test_EntityKey_no_name() -> None:
    type_ = "population"
    measure = "structure"
    key = EntityKey(f"{type_}.{measure}")

    assert key.type == type_
    assert key.name == ""
    assert key.measure == measure
    assert key.group_prefix == "/"
    assert key.group_name == type_
    assert key.group == f"/{type_}"
    assert key.path == f"/{type_}/{measure}"
    result = key.with_measure("age_groups")
    assert result == EntityKey("population.age_groups")
    assert isinstance(result, EntityKey)


def test_EntityKey_with_name() -> None:
    type_ = "cause"
    name = "diarrheal_diseases"
    measure = "incidence"
    key = EntityKey(f"{type_}.{name}.{measure}")

    assert key.type == type_
    assert key.name == name
    assert key.measure == measure
    assert key.group_prefix == f"/{type_}"
    assert key.group_name == name
    assert key.group == f"/{type_}/{name}"
    assert key.path == f"/{type_}/{name}/{measure}"
    result = key.with_measure("prevalence")
    assert result == EntityKey(f"{type_}.{name}.prevalence")
    assert isinstance(result, EntityKey)


def test_entity_key_equality() -> None:
    type_ = "cause"
    name = "diarrheal_diseases"
    measure = "incidence"
    string = f"{type_}.{name}.{measure}"
    key = EntityKey(string)

    class NonString:
        def __str__(self) -> str:
            return string

    nonstring = NonString()

    assert (
        key == string
    ), "Comparision using __eq__ between string object and equivalent EntityKey failed"
    assert not (
        key != string
    ), "Comparision using __ne__ between string object and equivalent EntityKey failed"
    assert (
        key != nonstring
    ), "Comparision using __eq__ between non-string object and equivalent EntityKey failed"
    assert not (
        key == nonstring
    ), "Comparision using __ne__ between non-string object and equivalent EntityKey failed"

    measure = "prevalence"
    string = f"{type_}.{name}.{measure}"

    assert (
        key != string
    ), "Comparision using __eq__ between string object and different EntityKey failed"
    assert not (
        key == string
    ), "Comparision using __ne__ between string object and different EntityKey failed"
    assert (
        key != nonstring
    ), "Comparision using __eq__ between non-string object and different EntityKey failed"
    assert not (
        key == nonstring
    ), "Comparision using __ne__ between non-string object and different EntityKey failed"
