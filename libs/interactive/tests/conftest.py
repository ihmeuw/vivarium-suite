from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from _pytest.logging import LogCaptureFixture
from loguru import logger
from vivarium.engine.examples.disease_model import get_model_specification_path

from vivarium.interactive import Simulation


@pytest.fixture(autouse=True)
def _clear_simulation_context_cache() -> None:
    Simulation._clear_context_cache()


@pytest.fixture
def caplog(caplog: LogCaptureFixture) -> Generator[LogCaptureFixture, None, None]:
    handler_id = logger.add(caplog.handler, format="{message}")
    yield caplog
    logger.remove(handler_id)


@pytest.fixture
def disease_model_spec(tmp_path: Path) -> Path:
    with open(get_model_specification_path(), "r") as file:
        ms = yaml.safe_load(file)

    # modify the time so as not to take so long for a unit test
    ms["configuration"]["time"]["end"]["year"] = ms["configuration"]["time"]["start"]["year"]
    ms["configuration"]["time"]["end"]["month"] = ms["configuration"]["time"]["start"][
        "month"
    ]
    ms["configuration"]["time"]["start"]["day"] = 1
    ms["configuration"]["time"]["end"]["day"] = 5
    ms["configuration"]["time"]["step_size"] = 0.5
    model_spec = tmp_path / "disease_model.yaml"

    with open(model_spec, "w") as file:
        yaml.dump(ms, file)

    return model_spec


class FakeWidget:
    def __init__(self, **kwargs: Any) -> None:
        self.bar_style = ""
        self.value: Any = None
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def progress_widgets(monkeypatch: pytest.MonkeyPatch) -> list[FakeWidget]:
    """Replace the notebook widgets log_progress draws with fakes and collect them."""
    created: list[FakeWidget] = []

    def make_widget(**kwargs: Any) -> FakeWidget:
        widget = FakeWidget(**kwargs)
        created.append(widget)
        return widget

    monkeypatch.setitem(
        sys.modules,
        "ipywidgets",
        SimpleNamespace(IntProgress=make_widget, HTML=make_widget, VBox=make_widget),
    )
    monkeypatch.setitem(sys.modules, "IPython", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "IPython.display", SimpleNamespace(display=lambda obj: None)
    )
    return created
