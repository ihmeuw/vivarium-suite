import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vivarium.engine.interface.utilities import get_output_model_name_string, log_progress

_MODEL_SPEC_STEM = "model_spec_name"
_ARTIFACT_STEM = "artifact_name"
_ARTIFACT_FROM_MODEL_SPEC_STEM = f"{_ARTIFACT_STEM}_from_model_spec"

_ARTIFACT_PATH = Path(f"/totally/fake/path/for/artifact/{_ARTIFACT_STEM}.hdf")
_MODEL_SPEC_ARTIFACT_PATH = Path(
    f"/totally/fake/path/for/artifact/{_ARTIFACT_FROM_MODEL_SPEC_STEM}.hdf"
)

_MODEL_SPEC_CONTENTS_WITHOUT = """
configuration:
    input_data:
        input_draw_number: 0
"""

_MODEL_SPEC_CONTENTS_WITH = (
    _MODEL_SPEC_CONTENTS_WITHOUT
    + f"""
        artifact_path: '{_MODEL_SPEC_ARTIFACT_PATH}'
"""
)


def _write_file(path: Path, contents: str) -> None:
    with open(path, "w") as file:
        file.write(contents)


@pytest.mark.parametrize(
    "artifact_path, model_spec_filename, contents, expected_output",
    [
        (
            # Given an input artifact path, use that
            _ARTIFACT_PATH,
            f"{_MODEL_SPEC_STEM}_with.yaml",
            _MODEL_SPEC_CONTENTS_WITH,
            _ARTIFACT_STEM,
        ),
        (
            # Without an input artifact path, but given model spec with artifact, use that
            None,
            f"{_MODEL_SPEC_STEM}_with.yaml",
            _MODEL_SPEC_CONTENTS_WITH,
            _ARTIFACT_FROM_MODEL_SPEC_STEM,
        ),
        (
            # Without the things in previous parameters, choose the stem of the model spec
            None,
            f"{_MODEL_SPEC_STEM}.yaml",
            _MODEL_SPEC_CONTENTS_WITHOUT,
            _MODEL_SPEC_STEM,
        ),
    ],
)
def test_get_output_model_name_string(
    artifact_path: Path,
    model_spec_filename: str,
    contents: str,
    expected_output: str,
    tmp_path: Path,
) -> None:
    model_spec_path = Path(f"{tmp_path}/{model_spec_filename}")
    _write_file(model_spec_path, contents)

    output = get_output_model_name_string(artifact_path, model_spec_path)

    assert output == expected_output


class _FakeWidget:
    def __init__(self, **kwargs: Any) -> None:
        self.bar_style = ""
        self.value: Any = None
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def progress_widgets(monkeypatch: pytest.MonkeyPatch) -> list[_FakeWidget]:
    """Replace the notebook widgets log_progress draws with fakes and collect them."""
    created: list[_FakeWidget] = []

    def make_widget(**kwargs: Any) -> _FakeWidget:
        widget = _FakeWidget(**kwargs)
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


def test_log_progress_finishes_when_stopped_early(
    progress_widgets: list[_FakeWidget],
) -> None:
    """A caller that stops iterating early still leaves the bar marked as finished."""
    steps = log_progress(range(5), name="Step")
    next(steps)
    next(steps)
    steps.close()

    progress = progress_widgets[0]
    assert progress.bar_style == "success"
    assert progress.value == 2
