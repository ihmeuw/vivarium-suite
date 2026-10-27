import sys
from types import SimpleNamespace
from typing import Any

import pytest


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
