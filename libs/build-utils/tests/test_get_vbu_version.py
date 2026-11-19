"""Tests for ``bootstrap/resources/scripts/get_vbu_version.py``.

Jenkins runs the script as a standalone file, so these tests load it by path. The
pip dry-run is never run: tests that reach it replace it with a stub.
"""
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).parents[1] / "bootstrap" / "resources" / "scripts" / "get_vbu_version.py"
)

GIT_SHA = "047f6b6322436c129750689618ff365ec21f82cf"

# Lock fragments copied from real `uv lock` output. The first package names vbu as a
# dependency, which the scan must not mistake for vbu's own entry.
LOCK_HEAD = """\
version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "probe"
version = "0.0.0"
source = { virtual = "." }
dependencies = [
    { name = "vivarium-build-utils" },
]

[package.metadata]
requires-dist = [{ name = "vivarium-build-utils" }]
"""

REGISTRY_ENTRY = """
[[package]]
name = "vivarium-build-utils"
version = "4.11.2"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "packaging" },
]
sdist = { url = "https://files.pythonhosted.org/vivarium_build_utils-4.11.2.tar.gz", hash = "sha256:00" }
"""

GIT_ENTRY = f"""
[[package]]
name = "vivarium-build-utils"
version = "4.11.3.dev1+g047f6b632"
source = {{ git = "https://github.com/ihmeuw/vivarium-suite.git?subdirectory=libs%2Fbuild-utils&rev=047f6b632#{GIT_SHA}" }}
dependencies = [
    {{ name = "packaging" }},
]
"""

PATH_ENTRY = """
[[package]]
name = "vivarium-build-utils"
version = "4.11.2"
source = { editable = "../vivarium-suite/libs/build-utils" }
"""

OTHER_ENTRY = """
[[package]]
name = "packaging"
version = "25.0"
source = { registry = "https://pypi.org/simple" }
"""


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Load the script fresh, run it in ``tmp_path``, and stub out the dry-run."""
    spec = importlib.util.spec_from_file_location("get_vbu_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_NAME", raising=False)
    (tmp_path / "python_versions.json").write_text('["3.11"]')
    monkeypatch.setattr(
        module, "_run_pip_dry_run", lambda _: " + vivarium-build-utils==4.9.1\n"
    )
    return module


@pytest.mark.parametrize(
    "lock, expected",
    [
        (LOCK_HEAD + REGISTRY_ENTRY + OTHER_ENTRY, "vivarium-build-utils-v4.11.2"),
        (LOCK_HEAD + GIT_ENTRY + OTHER_ENTRY, GIT_SHA),
        (LOCK_HEAD + OTHER_ENTRY, None),
        (LOCK_HEAD + PATH_ENTRY, None),
    ],
    ids=["registry", "git", "no-vbu-entry", "path-source"],
)
def test_get_locked_vbu_version(
    script: ModuleType, tmp_path: Path, lock: str, expected: str | None
) -> None:
    (tmp_path / "uv.lock").write_text(lock)
    assert script._get_locked_vbu_version() == expected


def test_get_locked_vbu_version_without_lock(script: ModuleType) -> None:
    assert script._get_locked_vbu_version() is None


@pytest.mark.parametrize(
    "lock, expected",
    [
        (LOCK_HEAD + REGISTRY_ENTRY, "vivarium-build-utils-v4.11.2"),
        (LOCK_HEAD + GIT_ENTRY, GIT_SHA),
        (LOCK_HEAD + OTHER_ENTRY, "vivarium-build-utils-v4.9.1"),
        (None, "vivarium-build-utils-v4.9.1"),
    ],
    ids=["registry", "git", "no-vbu-entry", "no-lock"],
)
def test_main_standalone(
    script: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    lock: str | None,
    expected: str,
) -> None:
    if lock is not None:
        (tmp_path / "uv.lock").write_text(lock)
    assert script.main() == expected
    # Jenkins reads the version from stdout, so nothing else may be printed there.
    assert capsys.readouterr().out == f"{expected}\n"


def test_main_monorepo_ignores_lock(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A lock in a monorepo lib's directory must not change what it resolves.
    lib = tmp_path / "libs" / "engine"
    lib.mkdir(parents=True)
    (lib / "python_versions.json").write_text('["3.11"]')
    (lib / "uv.lock").write_text(LOCK_HEAD + GIT_ENTRY)
    (tmp_path / "uv.lock").write_text(LOCK_HEAD + GIT_ENTRY)
    monkeypatch.setenv("JOB_NAME", "vivarium-suite/libs/engine/main")
    assert script.main() == "vivarium-build-utils-v4.9.1"
