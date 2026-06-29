"""Tests for the in-tree dependency graph (``vivarium.build_utils.dependencies``)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet

from vivarium.build_utils import dependencies
from vivarium.build_utils.dependencies import (
    DependencyConflictError,
    DependencyCycleError,
    InstallPlan,
    Lib,
    build_install_plan,
    get_editable_siblings,
    get_reachable_siblings,
    get_release_matrix,
    get_release_order,
    load_libs,
)

# A factory: given a mapping of package-name -> spec, write a throwaway monorepo
# under a temp dir and return its ``libs/`` path. Each spec carries the
# distribution name, the pending CHANGELOG version, and the package's declared
# dependencies (runtime and per-extra).
MonorepoFactory = Callable[[Mapping[str, Mapping[str, Any]]], Path]


def _render_dep_array(deps: Sequence[str]) -> str:
    """Render a list of requirement strings as a TOML array body."""
    return ", ".join(f'"{dep}"' for dep in deps)


@pytest.fixture
def make_monorepo(tmp_path: Path) -> MonorepoFactory:
    """Return a factory that writes a synthetic ``libs/`` tree and returns its path.

    The factory takes a mapping of directory-name -> spec. Each spec is a dict
    with optional keys:

    - ``version`` (str, default ``"1.0.0"``): the pending CHANGELOG version.
    - ``dist_name`` (str, default ``"vivarium-<name>"``): the ``[project].name``.
    - ``deps`` (Sequence[str], default ``[]``): runtime ``[project.dependencies]``.
    - ``extras`` (Mapping[str, Sequence[str]], default ``{}``): entries for
      ``[project.optional-dependencies]``.
    - ``changelog_first_line`` (str): override the entire CHANGELOG first line
      (e.g. to write a malformed version for the error path).
    - ``omit_name`` (bool, default ``False``): write a ``pyproject.toml`` with no
      ``[project].name`` line (for the unparseable-dist-name error path).

    Returns the ``libs/`` Path.
    """

    def factory(spec: Mapping[str, Mapping[str, Any]]) -> Path:
        libs_dir = tmp_path / "libs"
        libs_dir.mkdir(exist_ok=True)
        for name, cfg in spec.items():
            pkg_dir = libs_dir / name
            pkg_dir.mkdir(parents=True, exist_ok=True)

            dist_name = cfg.get("dist_name", f"vivarium-{name}")
            deps = cfg.get("deps", [])
            extras: Mapping[str, Sequence[str]] = cfg.get("extras", {})

            lines = [
                "[build-system]",
                'requires = ["setuptools>=61.0"]',
                'build-backend = "setuptools.build_meta"',
                "",
                "[project]",
            ]
            if not cfg.get("omit_name", False):
                lines.append(f'name = "{dist_name}"')
            lines += [
                'version = "0.0.0"',
                f"dependencies = [{_render_dep_array(deps)}]",
            ]
            if extras:
                lines.append("")
                lines.append("[project.optional-dependencies]")
                for extra_name, extra_deps in extras.items():
                    lines.append(f"{extra_name} = [{_render_dep_array(extra_deps)}]")
            (pkg_dir / "pyproject.toml").write_text("\n".join(lines) + "\n")

            if "changelog_first_line" in cfg:
                first_line = cfg["changelog_first_line"]
            else:
                version = cfg.get("version", "1.0.0")
                first_line = f"**{version} - 06/24/26**"
            (pkg_dir / "CHANGELOG.rst").write_text(first_line + "\n\n- Initial.\n")

        return libs_dir

    return factory


# --------------------------------------------------------------------------- #
# load_libs                                                                    #
# --------------------------------------------------------------------------- #
def test_load_libs_parses_dist_name_and_pending_version(
    make_monorepo: MonorepoFactory,
) -> None:
    """load_libs reads dist_name from [project].name and version from the CHANGELOG first line."""
    libs_dir = make_monorepo({"engine": {"dist_name": "vivarium-engine", "version": "2.3.4"}})
    libs = load_libs(libs_dir)
    engine = libs["engine"]
    assert engine.name == "engine"
    assert engine.dist_name == "vivarium-engine"
    assert engine.version == "2.3.4"
    assert engine.path == libs_dir / "engine"


def test_load_libs_collects_only_in_tree_sibling_deps(
    make_monorepo: MonorepoFactory,
) -> None:
    """sibling_deps contains in-tree deps only; external deps (numpy, dill, ...) are omitted."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b>=2.0.0", "numpy>=1.0", "dill"]},
            "b": {},
        }
    )
    libs = load_libs(libs_dir)
    assert set(libs["a"].sibling_deps) == {"vivarium-b"}
    assert libs["b"].sibling_deps == {}


def test_load_libs_expands_self_referential_extras(
    make_monorepo: MonorepoFactory,
) -> None:
    """A ci_github = [pkg[test,docs]] extra is expanded recursively into the underlying sibling deps."""
    libs_dir = make_monorepo(
        {
            "a": {
                "deps": [],
                "extras": {
                    "ci_github": ["vivarium-a[test]"],
                    "test": ["vivarium-b>=2.0.0"],
                },
            },
            "b": {},
        }
    )
    libs = load_libs(libs_dir, extras=("ci_github",))
    assert "vivarium-b" in libs["a"].sibling_deps
    assert SpecifierSet(">=2.0.0") == libs["a"].sibling_deps["vivarium-b"]


def test_load_libs_combines_specifiers_for_a_repeated_dep(
    make_monorepo: MonorepoFactory,
) -> None:
    """A sibling declared across runtime and extras yields a single combined SpecifierSet."""
    libs_dir = make_monorepo(
        {
            "a": {
                "deps": ["vivarium-b>=2.0.0"],
                "extras": {"ci_github": ["vivarium-b<3.0.0"]},
            },
            "b": {},
        }
    )
    libs = load_libs(libs_dir, extras=("ci_github",))
    combined = libs["a"].sibling_deps["vivarium-b"]
    assert combined == SpecifierSet(">=2.0.0,<3.0.0")
    assert combined.contains("2.5.0")
    assert not combined.contains("3.0.0")
    assert not combined.contains("1.9.0")


def test_load_libs_unpinned_dep_yields_match_all_specifier(
    make_monorepo: MonorepoFactory,
) -> None:
    """A sibling declared with no version pin yields an empty (match-all) SpecifierSet."""
    libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
    spec = load_libs(libs_dir)["a"].sibling_deps["vivarium-b"]
    assert spec == SpecifierSet()
    assert spec.contains("0.0.1")
    assert spec.contains("99.0.0")


def test_load_libs_raises_on_unparseable_version(
    make_monorepo: MonorepoFactory,
) -> None:
    """A CHANGELOG first line with no X.Y.Z raises ValueError."""
    libs_dir = make_monorepo({"a": {"changelog_first_line": "**not a version - 06/24/26**"}})
    with pytest.raises(ValueError):
        load_libs(libs_dir)


def test_load_libs_raises_on_missing_libs_dir(tmp_path: Path) -> None:
    """A non-existent libs_dir raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_libs(tmp_path / "does-not-exist")


def test_load_libs_raises_on_unparseable_dist_name(
    make_monorepo: MonorepoFactory,
) -> None:
    """A pyproject with no parseable [project].name raises ValueError."""
    libs_dir = make_monorepo({"a": {"omit_name": True}})
    with pytest.raises(ValueError):
        load_libs(libs_dir)


def test_load_libs_canonicalizes_dep_name_to_sibling(
    make_monorepo: MonorepoFactory,
) -> None:
    """A dep named with a case/underscore variant resolves to the canonical in-tree sibling."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["Vivarium_B>=2.0.0"]},
            "b": {"dist_name": "vivarium-b"},
        }
    )
    libs = load_libs(libs_dir)
    assert set(libs["a"].sibling_deps) == {"vivarium-b"}
    assert libs["a"].sibling_deps["vivarium-b"] == SpecifierSet(">=2.0.0")


# --------------------------------------------------------------------------- #
# get_reachable_siblings                                                           #
# --------------------------------------------------------------------------- #
def test_get_reachable_siblings_includes_transitive_deps(
    make_monorepo: MonorepoFactory,
) -> None:
    """Reachability follows in-tree edges transitively (a -> b -> c reaches both b and c)."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-c"]},
            "c": {},
        }
    )
    libs = load_libs(libs_dir)
    assert get_reachable_siblings("a", libs) == {"b", "c"}


def test_get_reachable_siblings_excludes_unrelated_libs(
    make_monorepo: MonorepoFactory,
) -> None:
    """A package the target does not depend on (directly or transitively) is not reachable."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
            "unrelated": {},
        }
    )
    libs = load_libs(libs_dir)
    assert get_reachable_siblings("a", libs) == {"b"}


def test_get_reachable_siblings_includes_extra_only_deps(
    make_monorepo: MonorepoFactory,
) -> None:
    """A dep activated only via the resolved extra (e.g. testing-utils via [test]) is reachable."""
    libs_dir = make_monorepo(
        {
            "a": {"extras": {"ci_github": ["vivarium-testing-utils"]}},
            "testing-utils": {"dist_name": "vivarium-testing-utils"},
        }
    )
    libs = load_libs(libs_dir, extras=("ci_github",))
    assert get_reachable_siblings("a", libs) == {"testing-utils"}


def test_get_reachable_siblings_excludes_target_itself(
    make_monorepo: MonorepoFactory,
) -> None:
    """The target is not in its own reachable set."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
        }
    )
    libs = load_libs(libs_dir)
    assert "a" not in get_reachable_siblings("a", libs)


# --------------------------------------------------------------------------- #
# get_editable_siblings                                                            #
# --------------------------------------------------------------------------- #
def test_get_editable_siblings_selects_changed_reachable_compatible(
    make_monorepo: MonorepoFactory,
) -> None:
    """A changed, reachable, version-compatible sibling is selected."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b>=2.0.0"]},
            "b": {"version": "2.1.0"},
        }
    )
    libs = load_libs(libs_dir)
    selected = get_editable_siblings("a", libs, changed=["b"])
    assert [lib.name for lib in selected] == ["b"]


def test_get_editable_siblings_excludes_unchanged_reachable_dep(
    make_monorepo: MonorepoFactory,
) -> None:
    """A reachable dep that did not change in the PR is not selected (resolves from PyPI)."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
        }
    )
    libs = load_libs(libs_dir)
    assert get_editable_siblings("a", libs, changed=[]) == []


def test_get_editable_siblings_excludes_changed_unreachable_lib(
    make_monorepo: MonorepoFactory,
) -> None:
    """A changed package the target does not depend on is not selected."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
            "unrelated": {},
        }
    )
    libs = load_libs(libs_dir)
    selected = get_editable_siblings("a", libs, changed=["unrelated"])
    assert selected == []


def test_get_editable_siblings_ordered_dependencies_first(
    make_monorepo: MonorepoFactory,
) -> None:
    """Selected siblings are ordered so each follows the selected siblings it depends on."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-c"]},
            "c": {},
        }
    )
    libs = load_libs(libs_dir)
    selected = get_editable_siblings("a", libs, changed=["b", "c"])
    names = [lib.name for lib in selected]
    assert set(names) == {"b", "c"}
    assert names.index("c") < names.index("b")


def test_get_editable_siblings_empty_when_no_changed_siblings(
    make_monorepo: MonorepoFactory,
) -> None:
    """No changed packages (or none reachable) yields an empty selection."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
        }
    )
    libs = load_libs(libs_dir)
    assert get_editable_siblings("a", libs, changed=[]) == []


def test_get_editable_siblings_hard_fails_on_pin_conflict(
    make_monorepo: MonorepoFactory,
) -> None:
    """A selected sibling whose pending version violates a reachable pin raises DependencyConflictError."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b<2.0.0"]},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    with pytest.raises(DependencyConflictError):
        get_editable_siblings("a", libs, changed=["b"])


def test_get_editable_siblings_conflict_message_names_sibling_version_and_pin(
    make_monorepo: MonorepoFactory,
) -> None:
    """The DependencyConflictError message identifies the sibling, its pending version, and the conflicting pin."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b<2.0.0"]},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    with pytest.raises(DependencyConflictError) as exc_info:
        get_editable_siblings("a", libs, changed=["b"])
    message = str(exc_info.value)
    assert "vivarium-b" in message
    assert "2.0.0" in message
    assert "<2.0.0" in message


def test_get_editable_siblings_detects_transitive_conflict(
    make_monorepo: MonorepoFactory,
) -> None:
    """A pin on a changed transitive sibling (from a non-target intermediate) raises DependencyConflictError."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-c<2.0.0"]},
            "c": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    with pytest.raises(DependencyConflictError):
        get_editable_siblings("a", libs, changed=["b", "c"])


def test_get_editable_siblings_raises_keyerror_for_unknown_target(
    make_monorepo: MonorepoFactory,
) -> None:
    """An unknown target name raises KeyError."""
    libs_dir = make_monorepo({"a": {}})
    libs = load_libs(libs_dir)
    with pytest.raises(KeyError):
        get_editable_siblings("nonexistent", libs, changed=[])


def test_get_editable_siblings_raises_keyerror_for_unknown_changed(
    make_monorepo: MonorepoFactory,
) -> None:
    """An unknown package in `changed` raises KeyError."""
    libs_dir = make_monorepo({"a": {}})
    libs = load_libs(libs_dir)
    with pytest.raises(KeyError):
        get_editable_siblings("a", libs, changed=["ghost"])


# --------------------------------------------------------------------------- #
# get_release_order                                                                #
# --------------------------------------------------------------------------- #
def test_get_release_order_dependencies_first(make_monorepo: MonorepoFactory) -> None:
    """A dependency is ordered before its dependent."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {},
        }
    )
    libs = load_libs(libs_dir)
    ordered = get_release_order(["a", "b"], libs)
    assert ordered.index("b") < ordered.index("a")


def test_get_release_order_preserves_input_order_for_independent_packages(
    make_monorepo: MonorepoFactory,
) -> None:
    """Packages with no dependency relationship keep their input order."""
    libs_dir = make_monorepo({"a": {}, "b": {}, "c": {}})
    libs = load_libs(libs_dir)
    assert get_release_order(["c", "a", "b"], libs) == ["c", "a", "b"]


def test_get_release_order_ignores_packages_outside_the_batch(
    make_monorepo: MonorepoFactory,
) -> None:
    """Only the named packages are ordered; out-of-batch deps do not appear in the result."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-c"]},
            "c": {},
        }
    )
    libs = load_libs(libs_dir)
    ordered = get_release_order(["a", "b"], libs)
    assert "c" not in ordered
    assert ordered.index("b") < ordered.index("a")


def test_get_release_order_raises_on_cycle(make_monorepo: MonorepoFactory) -> None:
    """A dependency cycle among the named packages raises DependencyCycleError."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-a"]},
        }
    )
    libs = load_libs(libs_dir)
    with pytest.raises(DependencyCycleError):
        get_release_order(["a", "b"], libs)


# --------------------------------------------------------------------------- #
# get_release_matrix                                                               #
# --------------------------------------------------------------------------- #
def test_get_release_matrix_include_ordered_dependencies_first(
    make_monorepo: MonorepoFactory,
) -> None:
    """include entries are ordered dependencies-first."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    matrix = get_release_matrix({"a": "1.0.0", "b": "2.0.0"}, libs)
    libraries = [entry["library"] for entry in matrix["include"]]
    assert libraries.index("b") < libraries.index("a")


def test_get_release_matrix_wait_for_lists_in_batch_upstreams(
    make_monorepo: MonorepoFactory,
) -> None:
    """A dependent's wait_for lists each upstream that is also in the release batch, as dist==version."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    matrix = get_release_matrix({"a": "1.0.0", "b": "2.0.0"}, libs)
    entry = next(e for e in matrix["include"] if e["library"] == "a")
    assert entry["wait_for"] == [{"dist": "vivarium-b", "version": "2.0.0"}]


def test_get_release_matrix_wait_for_empty_for_independent_package(
    make_monorepo: MonorepoFactory,
) -> None:
    """A package with no in-batch upstream has an empty wait_for."""
    libs_dir = make_monorepo({"a": {"version": "1.0.0"}, "b": {"version": "2.0.0"}})
    libs = load_libs(libs_dir)
    matrix = get_release_matrix({"a": "1.0.0", "b": "2.0.0"}, libs)
    for entry in matrix["include"]:
        assert entry["wait_for"] == []


def test_get_release_matrix_omits_out_of_batch_upstream_from_wait_for(
    make_monorepo: MonorepoFactory,
) -> None:
    """An upstream not in the release batch (already released) is not in wait_for."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    matrix = get_release_matrix({"a": "1.0.0"}, libs)
    entry = next(e for e in matrix["include"] if e["library"] == "a")
    assert entry["wait_for"] == []


def test_get_release_matrix_empty_pairs_yields_empty_include(
    make_monorepo: MonorepoFactory,
) -> None:
    """An empty pairs mapping yields {"include": []}."""
    libs_dir = make_monorepo({"a": {}})
    libs = load_libs(libs_dir)
    assert get_release_matrix({}, libs) == {"include": []}


def test_get_release_matrix_wait_for_is_transitive_closure(
    make_monorepo: MonorepoFactory,
) -> None:
    """For chain a -> b -> c all in batch, wait_for is the transitive in-batch closure."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"deps": ["vivarium-c"], "version": "2.0.0"},
            "c": {"version": "3.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    matrix = get_release_matrix({"a": "1.0.0", "b": "2.0.0", "c": "3.0.0"}, libs)
    entries = {e["library"]: e for e in matrix["include"]}

    order = [e["library"] for e in matrix["include"]]
    assert order.index("c") < order.index("b") < order.index("a")

    def _wait_dists(library: str) -> set[str]:
        return {w["dist"] for w in entries[library]["wait_for"]}

    assert _wait_dists("a") == {"vivarium-b", "vivarium-c"}
    assert _wait_dists("b") == {"vivarium-c"}
    assert entries["c"]["wait_for"] == []
    assert {"dist": "vivarium-c", "version": "3.0.0"} in entries["a"]["wait_for"]
    assert {"dist": "vivarium-b", "version": "2.0.0"} in entries["a"]["wait_for"]


def test_get_release_matrix_raises_keyerror_for_unknown_package(
    make_monorepo: MonorepoFactory,
) -> None:
    """A pairs key that is not a known package raises KeyError."""
    libs_dir = make_monorepo({"a": {}})
    libs = load_libs(libs_dir)
    with pytest.raises(KeyError):
        get_release_matrix({"ghost": "1.0.0"}, libs)


# --------------------------------------------------------------------------- #
# build_install_plan                                                           #
# --------------------------------------------------------------------------- #
def _make_lib(
    name: str,
    *,
    dist_name: str | None = None,
    path: Path | None = None,
    version: str = "1.0.0",
    sibling_deps: Mapping[str, SpecifierSet] | None = None,
) -> Lib:
    """Construct a Lib directly for plan tests."""
    return Lib(
        name=name,
        dist_name=dist_name or f"vivarium-{name}",
        path=path or Path("/repo/libs") / name,
        version=version,
        sibling_deps=sibling_deps or {},
    )


def test_build_install_plan_installs_target_editable_with_extras() -> None:
    """The plan installs the target editably with its [env_reqs] extra."""
    target = _make_lib("a", path=Path("/repo/libs/a"))
    plan = build_install_plan(target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="")
    argv = list(plan.argv)
    assert "-e" in argv
    joined = " ".join(argv)
    assert f"-e {target.path}[ci_github]" in joined


def test_build_install_plan_installs_each_sibling_editable() -> None:
    """Each selected sibling appears as an editable (-e) install of its in-tree path."""
    target = _make_lib("a", path=Path("/repo/libs/a"))
    b_path = Path("/repo/libs/b")
    c_path = Path("/repo/libs/c")
    siblings = [_make_lib("c", path=c_path), _make_lib("b", path=b_path)]
    plan = build_install_plan(
        target, siblings, env_reqs="ci_github", ihme_pypi="", uv_flags=""
    )
    joined = " ".join(plan.argv)
    assert str(b_path) in joined
    assert str(c_path) in joined


def test_build_install_plan_is_a_single_uv_invocation() -> None:
    """Target and all siblings are installed in one uv pip install command (no clobber)."""
    target = _make_lib("a")
    siblings = [_make_lib("b"), _make_lib("c")]
    plan = build_install_plan(
        target, siblings, env_reqs="ci_github", ihme_pypi="", uv_flags=""
    )
    argv = list(plan.argv)
    assert argv[:3] == ["uv", "pip", "install"]
    # only one install verb in the whole command
    assert argv.count("install") == 1


def test_build_install_plan_sets_pretend_version_per_sibling() -> None:
    """env carries SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>=<pending version> for each sibling."""
    target = _make_lib("a")
    siblings = [
        _make_lib("engine", dist_name="vivarium-engine", version="2.3.4"),
        _make_lib("public-health", dist_name="vivarium-public-health", version="5.6.7"),
    ]
    plan = build_install_plan(
        target, siblings, env_reqs="ci_github", ihme_pypi="", uv_flags=""
    )
    assert plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_ENGINE"] == "2.3.4"
    assert plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_PUBLIC_HEALTH"] == "5.6.7"


def test_build_install_plan_applies_editable_compat_per_package() -> None:
    """Each editable package carries the editable_mode=compat config setting."""
    target = _make_lib("a", dist_name="vivarium-a")
    siblings = [_make_lib("b", dist_name="vivarium-b")]
    plan = build_install_plan(
        target, siblings, env_reqs="ci_github", ihme_pypi="", uv_flags=""
    )
    joined = " ".join(plan.argv)
    assert "vivarium-a:editable_mode=compat" in joined
    assert "vivarium-b:editable_mode=compat" in joined


def test_build_install_plan_includes_extra_index_when_ihme_pypi_set() -> None:
    """A non-empty ihme_pypi adds the extra-index-url and index-strategy flags."""
    target = _make_lib("a")
    plan = build_install_plan(
        target,
        [],
        env_reqs="ci_github",
        ihme_pypi="https://artifactory.ihme.example/api/pypi/pypi/simple",
        uv_flags="",
    )
    joined = " ".join(plan.argv)
    assert "https://artifactory.ihme.example/api/pypi/pypi/simple" in joined
    assert "--extra-index-url" in plan.argv
    assert "--index-strategy" in plan.argv


def test_build_install_plan_omits_extra_index_when_ihme_pypi_empty() -> None:
    """An empty ihme_pypi produces no extra-index flags."""
    target = _make_lib("a")
    plan = build_install_plan(target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="")
    assert "--extra-index-url" not in plan.argv


def test_build_install_plan_passes_through_uv_flags() -> None:
    """uv_flags tokens are appended to argv; a blank uv_flags adds nothing."""
    target = _make_lib("a")
    with_flags = build_install_plan(
        target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="--system --no-cache"
    )
    assert "--system" in with_flags.argv
    assert "--no-cache" in with_flags.argv

    blank = build_install_plan(target, [], env_reqs="ci_github", ihme_pypi="", uv_flags=" ")
    assert "--system" not in blank.argv
    assert "" not in blank.argv


# --------------------------------------------------------------------------- #
# run_install                                                                  #
# --------------------------------------------------------------------------- #
def test_run_install_invokes_subprocess_with_cwd_check_and_overlaid_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """run_install runs argv via subprocess with cwd=libs_dir, check=True, and env overlaid on os.environ."""
    import subprocess

    monkeypatch.setenv("PRE_EXISTING_KEY", "keep-me")
    captured: dict[str, Any] = {}

    def fake_run(argv: Sequence[str], **kwargs: Any) -> None:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "run", fake_run)

    libs_dir = tmp_path / "libs"
    libs_dir.mkdir()
    plan = InstallPlan(
        argv=["uv", "pip", "install", "-e", str(libs_dir / "a")],
        env={"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_A": "1.0.0"},
    )
    dependencies.run_install(plan, libs_dir)

    assert captured["argv"] == list(plan.argv)
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == libs_dir
    assert kwargs["check"] is True
    env = kwargs["env"]
    assert env["PRE_EXISTING_KEY"] == "keep-me"
    assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_A"] == "1.0.0"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def test_cli_get_release_matrix_emits_ordered_json(
    make_monorepo: MonorepoFactory,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """`release-matrix` prints dependency-ordered matrix JSON to stdout."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"version": "2.0.0"},
        }
    )
    pairs_file = tmp_path / "pairs.txt"
    pairs_file.write_text("a 1.0.0\nb 2.0.0\n")
    exit_code = main_with(
        [
            "release-matrix",
            "--versions",
            str(pairs_file),
            "--libs-dir",
            str(libs_dir),
        ]
    )
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    libraries = [entry["library"] for entry in out["include"]]
    assert libraries.index("b") < libraries.index("a")


def test_cli_get_release_matrix_empty_when_no_pairs(
    make_monorepo: MonorepoFactory,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """`release-matrix` with no pairs prints {"include": []} and exits 0."""
    libs_dir = make_monorepo({"a": {}})
    pairs_file = tmp_path / "pairs.txt"
    pairs_file.write_text("")
    exit_code = main_with(
        ["release-matrix", "--versions", str(pairs_file), "--libs-dir", str(libs_dir)]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"include": []}


def test_cli_get_release_matrix_errors_on_missing_version(
    make_monorepo: MonorepoFactory,
    tmp_path: Path,
) -> None:
    """`release-matrix` exits non-zero when a pairs line has no version."""
    libs_dir = make_monorepo({"a": {}})
    pairs_file = tmp_path / "pairs.txt"
    pairs_file.write_text("a\n")  # no version provided!
    exit_code = main_with(
        ["release-matrix", "--versions", str(pairs_file), "--libs-dir", str(libs_dir)]
    )
    assert exit_code != 0


def test_cli_editable_install_runs_selected_plan(
    make_monorepo: MonorepoFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`editable-install` builds the plan for the selected siblings and runs it (runner patched)."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b>=2.0.0"]},
            "b": {"version": "2.1.0"},
        }
    )
    captured: dict[str, Any] = {}

    def fake_run_install(plan: InstallPlan, run_dir: Path) -> None:
        captured["plan"] = plan
        captured["libs_dir"] = run_dir

    monkeypatch.setattr(dependencies, "run_install", fake_run_install)
    exit_code = main_with(
        [
            "editable-install",
            "a",
            "--changed",
            "b",
            "--env-reqs",
            "ci_github",
            "--ihme-pypi",
            "",
            "--uv-flags",
            "",
            "--libs-dir",
            str(libs_dir),
        ]
    )
    assert exit_code == 0
    plan = captured["plan"]
    joined = " ".join(plan.argv)
    assert f"-e {libs_dir / 'a'}[ci_github]" in joined
    assert str(libs_dir / "b") in joined
    assert plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_B"] == "2.1.0"


def test_cli_editable_install_hard_fails_on_conflict(
    make_monorepo: MonorepoFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`editable-install` exits non-zero (without installing) on a version-pin conflict."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b<2.0.0"]},
            "b": {"version": "2.0.0"},
        }
    )
    called = False

    def fake_run_install(plan: InstallPlan, run_dir: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dependencies, "run_install", fake_run_install)
    exit_code = main_with(
        [
            "editable-install",
            "a",
            "--changed",
            "b",
            "--env-reqs",
            "ci_github",
            "--ihme-pypi",
            "",
            "--uv-flags",
            "",
            "--libs-dir",
            str(libs_dir),
        ]
    )
    assert exit_code != 0
    assert called is False


def test_cli_get_release_matrix_clean_exit_on_cycle(
    make_monorepo: MonorepoFactory,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """`release-matrix` on a cyclic batch exits non-zero with a clean message (no traceback)."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
            "b": {"deps": ["vivarium-a"], "version": "2.0.0"},
        }
    )
    pairs_file = tmp_path / "pairs.txt"
    pairs_file.write_text("a 1.0.0\nb 2.0.0\n")
    exit_code = main_with(
        ["release-matrix", "--versions", str(pairs_file), "--libs-dir", str(libs_dir)]
    )
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "Traceback" not in (captured.out + captured.err)


def test_cli_editable_install_clean_exit_on_cycle(
    make_monorepo: MonorepoFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`editable-install` exits non-zero with a clean message (no traceback, no install) on a cycle."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b"]},
            "b": {"deps": ["vivarium-c"]},
            "c": {"deps": ["vivarium-b"]},
        }
    )
    called = False

    def fake_run_install(plan: InstallPlan, run_dir: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dependencies, "run_install", fake_run_install)
    exit_code = main_with(
        [
            "editable-install",
            "a",
            "--changed",
            "b c",
            "--env-reqs",
            "ci_github",
            "--ihme-pypi",
            "",
            "--uv-flags",
            "",
            "--libs-dir",
            str(libs_dir),
        ]
    )
    assert exit_code != 0
    assert called is False
    captured = capsys.readouterr()
    assert "Traceback" not in (captured.out + captured.err)


# --------------------------------------------------------------------------- #
# End-to-end                                                                   #
# --------------------------------------------------------------------------- #
def test_end_to_end_changed_upstream_bump_installs_editable_before_dependent(
    make_monorepo: MonorepoFactory,
) -> None:
    """For target A depending on changed+bumped B: the plan installs B editably at its pending
    version, ordered before A, in a single uv invocation - the core cross-package PR scenario."""
    libs_dir = make_monorepo(
        {
            "a": {"deps": ["vivarium-b>=2.0.0"], "version": "1.0.0"},
            "b": {"version": "2.0.0"},
        }
    )
    libs = load_libs(libs_dir)
    siblings = get_editable_siblings("a", libs, changed=["b"])
    plan = build_install_plan(
        libs["a"], siblings, env_reqs="ci_github", ihme_pypi="", uv_flags=""
    )
    argv = list(plan.argv)
    # Single uv invocation: target and sibling resolved together (no clobber);
    # the order of the -e args within it is irrelevant to uv.
    assert argv[:3] == ["uv", "pip", "install"]
    assert argv.count("install") == 1
    joined = " ".join(argv)
    a_spec = f"-e {libs_dir / 'a'}[ci_github]"
    b_spec = f"-e {libs_dir / 'b'}"
    assert a_spec in joined
    assert b_spec in joined
    assert "vivarium-b:editable_mode=compat" in joined
    # B (the changed upstream) reports its pending version via setuptools_scm.
    assert plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_B"] == "2.0.0"


def main_with(argv: Sequence[str]) -> int:
    """Invoke the module's CLI ``main`` with an explicit argv list."""
    return dependencies.main(list(argv))


# --------------------------------------------------------------------------- #
# _discover_libs_dir (CLI default libs-dir resolution)                         #
# --------------------------------------------------------------------------- #
def test_discover_libs_dir_from_package_subdir(
    make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-discovery walks up from a package subdir to the libs/ dir holding build-utils."""
    libs_dir = make_monorepo(
        {"build-utils": {}, "engine": {"deps": ["vivarium-build-utils"]}}
    )
    monkeypatch.chdir(libs_dir / "engine")
    assert dependencies._discover_libs_dir(None) == libs_dir


def test_discover_libs_dir_from_repo_root(
    make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-discovery finds libs/ when run from the repo root (libs/'s parent)."""
    libs_dir = make_monorepo({"build-utils": {}})
    monkeypatch.chdir(libs_dir.parent)
    assert dependencies._discover_libs_dir(None) == libs_dir


def test_discover_libs_dir_falls_back_to_cwd_libs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no build-utils package anywhere above, discovery falls back to <cwd>/libs."""
    monkeypatch.chdir(tmp_path)
    assert dependencies._discover_libs_dir(None) == tmp_path / "libs"


def test_discover_libs_dir_honors_path_provided(tmp_path: Path) -> None:
    """A provided path is used verbatim (resolved), without walking."""
    assert dependencies._discover_libs_dir(str(tmp_path)) == tmp_path.resolve()


def test_cli_editable_install_clean_exit_on_unknown_package(
    make_monorepo: MonorepoFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """`editable-install` for a target not under libs/ exits non-zero with a clear message."""
    libs_dir = make_monorepo({"build-utils": {}, "a": {}})
    rc = main_with(["editable-install", "ghost", "--libs-dir", str(libs_dir)])
    assert rc == 1
    assert "unknown package" in capsys.readouterr().err


def test_cli_release_matrix_clean_exit_on_unknown_package(
    make_monorepo: MonorepoFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`release-matrix` with a pairs entry not under libs/ exits non-zero with a clear message."""
    libs_dir = make_monorepo({"build-utils": {}, "a": {}})
    pairs = tmp_path / "release_pairs.txt"
    pairs.write_text("ghost 1.0.0\n")  # non-monorepo package!
    rc = main_with(["release-matrix", "--versions", str(pairs), "--libs-dir", str(libs_dir)])
    assert rc == 1
    assert "unknown package" in capsys.readouterr().err


def test_cli_verify_editable_passes_when_siblings_editable(
    make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verify-editable` exits 0 when each selected sibling is an editable install."""
    libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
    monkeypatch.setattr(dependencies, "_is_editable_install", lambda dist: True)
    rc = main_with(["verify-editable", "a", "--changed", "b", "--libs-dir", str(libs_dir)])
    assert rc == 0


def test_cli_verify_editable_fails_when_sibling_from_pypi(
    make_monorepo: MonorepoFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`verify-editable` exits non-zero when a selected sibling is not an editable install."""
    libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
    monkeypatch.setattr(dependencies, "_is_editable_install", lambda dist: False)
    rc = main_with(["verify-editable", "a", "--changed", "b", "--libs-dir", str(libs_dir)])
    assert rc == 1
    assert "not editable" in capsys.readouterr().err


def test_cli_verify_editable_noop_when_no_siblings(
    make_monorepo: MonorepoFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """`verify-editable` exits 0 and checks nothing when the target has no changed siblings."""
    libs_dir = make_monorepo({"a": {}, "b": {}})
    rc = main_with(["verify-editable", "a", "--changed", "", "--libs-dir", str(libs_dir)])
    assert rc == 0
    assert "no changed in-tree siblings" in capsys.readouterr().out
