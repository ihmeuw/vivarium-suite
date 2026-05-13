from pathlib import Path

import pytest


@pytest.fixture
def test_data_dir() -> Path:
    data_dir = Path(__file__).resolve().parent / "test_data"
    assert data_dir.exists(), "Test directory structure is broken"
    return data_dir


@pytest.fixture(params=[".yaml", ".yml"])
def test_spec(request: pytest.FixtureRequest, test_data_dir: Path) -> Path:
    return test_data_dir / f"mock_model_specification{request.param}"
