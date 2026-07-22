"""Tests for the in-tree dependency graph (``vivarium.build_utils.dependency_graph``)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet

from vivarium.build_utils import dependency_graph
from vivarium.build_utils.dependency_graph import (
    DependencyConflictError,
    DependencyCycleError,
    InstallPlan,
    Lib,
    build_install_plan,
    get_editable_upstreams,
    get_release_matrix,
    get_transitive_upstreams,
    load_libs,
    sort_topologically,
)

# A factory: given a mapping of library-name -> spec, write a throwaway monorepo
# under a temp dir and return its ``libs/`` path. Each spec carries the
# distribution name, the pending CHANGELOG version, and the library's declared
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


def _make_lib(
    name: str,
    *,
    dist_name: str | None = None,
    path: Path | None = None,
    version: str = "1.0.0",
    upstreams: Mapping[str, SpecifierSet] | None = None,
) -> Lib:
    """Construct a Lib directly for plan tests."""
    return Lib(
        name=name,
        dist_name=dist_name or f"vivarium-{name}",
        path=path or Path("/repo/libs") / name,
        version=version,
        upstreams=upstreams or {},
    )


def main_with(argv: Sequence[str]) -> int:
    """Invoke the module's CLI ``main`` with an explicit argv list."""
    return dependency_graph.main(list(argv))


class TestLoadLibs:
    """Tests for ``load_libs``."""

    def test_parses_dist_name_and_pending_version(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """load_libs reads dist_name from [project].name and version from the CHANGELOG first line."""
        libs_dir = make_monorepo(
            {"engine": {"dist_name": "vivarium-engine", "version": "2.3.4"}}
        )
        libs = load_libs(libs_dir)
        engine = libs["engine"]
        assert engine.name == "engine"
        assert engine.dist_name == "vivarium-engine"
        assert engine.version == "2.3.4"
        assert engine.path == libs_dir / "engine"

    def test_collects_only_in_tree_upstreams(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """upstreams contains in-tree deps only; external deps (numpy, dill, ...) are omitted."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b>=2.0.0", "numpy>=1.0", "dill"]},
                "b": {},
            }
        )
        libs = load_libs(libs_dir)
        assert set(libs["a"].upstreams) == {"vivarium-b"}
        assert libs["b"].upstreams == {}

    def test_expands_self_referential_extras(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A ci_github = [pkg[test,docs]] extra is expanded recursively into the underlying upstream deps."""
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
        assert "vivarium-b" in libs["a"].upstreams
        assert SpecifierSet(">=2.0.0") == libs["a"].upstreams["vivarium-b"]

    def test_combines_specifiers_for_a_repeated_dep(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A upstream declared across runtime and extras yields a single combined SpecifierSet."""
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
        combined = libs["a"].upstreams["vivarium-b"]
        assert combined == SpecifierSet(">=2.0.0,<3.0.0")
        assert combined.contains("2.5.0")
        assert not combined.contains("3.0.0")
        assert not combined.contains("1.9.0")

    def test_unpinned_dep_yields_match_all_specifier(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A upstream declared with no version pin yields an empty (match-all) SpecifierSet."""
        libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
        spec = load_libs(libs_dir)["a"].upstreams["vivarium-b"]
        assert spec == SpecifierSet()
        assert spec.contains("0.0.1")
        assert spec.contains("99.0.0")

    def test_raises_on_unparseable_version(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A CHANGELOG first line with no X.Y.Z raises ValueError."""
        libs_dir = make_monorepo(
            {"a": {"changelog_first_line": "**not a version - 06/24/26**"}}
        )
        with pytest.raises(ValueError):
            load_libs(libs_dir)

    def test_raises_on_missing_libs_dir(self, tmp_path: Path) -> None:
        """A non-existent libs_dir raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_libs(tmp_path / "does-not-exist")

    def test_raises_on_unparseable_dist_name(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A pyproject with no parseable [project].name raises ValueError."""
        libs_dir = make_monorepo({"a": {"omit_name": True}})
        with pytest.raises(ValueError):
            load_libs(libs_dir)

    def test_canonicalizes_dep_name_to_upstream(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A dep named with a case/underscore variant resolves to the canonical in-tree upstream."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["Vivarium_B>=2.0.0"]},
                "b": {"dist_name": "vivarium-b"},
            }
        )
        libs = load_libs(libs_dir)
        assert set(libs["a"].upstreams) == {"vivarium-b"}
        assert libs["a"].upstreams["vivarium-b"] == SpecifierSet(">=2.0.0")


class TestGetTransitiveUpstreams:
    """Tests for ``get_transitive_upstreams``."""

    def test_includes_transitive_deps(
        self,
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
        assert get_transitive_upstreams("a", libs) == {"b", "c"}

    def test_excludes_unrelated_libs(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A library the target does not depend on (directly or transitively) is not reachable."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {},
                "unrelated": {},
            }
        )
        libs = load_libs(libs_dir)
        assert get_transitive_upstreams("a", libs) == {"b"}

    def test_includes_extra_only_deps(
        self,
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
        assert get_transitive_upstreams("a", libs) == {"testing-utils"}

    def test_excludes_target_itself(
        self,
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
        assert "a" not in get_transitive_upstreams("a", libs)


class TestGetEditableUpstreams:
    """Tests for ``get_editable_upstreams``."""

    def test_selects_changed_reachable_compatible(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A changed, reachable, version-compatible upstream is selected."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b>=2.0.0"]},
                "b": {"version": "2.1.0"},
            }
        )
        libs = load_libs(libs_dir)
        selected = get_editable_upstreams("a", libs, changed=["b"])
        assert [lib.name for lib in selected] == ["b"]

    def test_excludes_unchanged_reachable_dep(
        self,
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
        assert get_editable_upstreams("a", libs, changed=[]) == []

    def test_excludes_changed_unreachable_lib(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A changed library the target does not depend on is not selected."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {},
                "unrelated": {},
            }
        )
        libs = load_libs(libs_dir)
        selected = get_editable_upstreams("a", libs, changed=["unrelated"])
        assert selected == []

    def test_selects_transitively_reachable_changed(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """Selection includes changed upstreams reachable transitively (a -> b -> c selects both b and c)."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {"deps": ["vivarium-c"]},
                "c": {},
            }
        )
        libs = load_libs(libs_dir)
        selected = get_editable_upstreams("a", libs, changed=["b", "c"])
        assert {lib.name for lib in selected} == {"b", "c"}

    def test_empty_when_no_changed_upstreams(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """No changed libraries (or none reachable) yields an empty selection."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {},
            }
        )
        libs = load_libs(libs_dir)
        assert get_editable_upstreams("a", libs, changed=[]) == []

    def test_hard_fails_on_pin_conflict(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A selected upstream whose pending version violates a reachable pin raises DependencyConflictError."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b<2.0.0"]},
                "b": {"version": "2.0.0"},
            }
        )
        libs = load_libs(libs_dir)
        with pytest.raises(DependencyConflictError):
            get_editable_upstreams("a", libs, changed=["b"])

    def test_conflict_message_names_upstream_version_and_pin(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """The DependencyConflictError message identifies the upstream, its pending version, and the conflicting pin."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b<2.0.0"]},
                "b": {"version": "2.0.0"},
            }
        )
        libs = load_libs(libs_dir)
        with pytest.raises(DependencyConflictError) as exc_info:
            get_editable_upstreams("a", libs, changed=["b"])
        message = str(exc_info.value)
        assert "vivarium-b" in message
        assert "2.0.0" in message
        assert "<2.0.0" in message

    def test_detects_transitive_conflict(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A pin on a changed transitive upstream (from a non-target intermediate) raises DependencyConflictError."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {"deps": ["vivarium-c<2.0.0"]},
                "c": {"version": "2.0.0"},
            }
        )
        libs = load_libs(libs_dir)
        with pytest.raises(DependencyConflictError):
            get_editable_upstreams("a", libs, changed=["b", "c"])

    def test_raises_keyerror_for_unknown_target(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """An unknown target name raises KeyError."""
        libs_dir = make_monorepo({"a": {}})
        libs = load_libs(libs_dir)
        with pytest.raises(KeyError):
            get_editable_upstreams("nonexistent", libs, changed=[])

    def test_raises_keyerror_for_unknown_changed(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """An unknown library in `changed` raises KeyError."""
        libs_dir = make_monorepo({"a": {}})
        libs = load_libs(libs_dir)
        with pytest.raises(KeyError):
            get_editable_upstreams("a", libs, changed=["ghost"])


class TestSortTopologically:
    """Tests for ``sort_topologically``."""

    def test_dependencies_first(self, make_monorepo: MonorepoFactory) -> None:
        """A dependency is ordered before its dependent."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {},
            }
        )
        libs = load_libs(libs_dir)
        ordered = sort_topologically(["a", "b"], libs)
        assert ordered.index("b") < ordered.index("a")

    def test_preserves_input_order_for_independent_libraries(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """Libraries with no dependency relationship keep their input order."""
        libs_dir = make_monorepo({"a": {}, "b": {}, "c": {}})
        libs = load_libs(libs_dir)
        assert sort_topologically(["c", "a", "b"], libs) == ["c", "a", "b"]

    def test_ignores_libraries_outside_the_batch(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """Only the named libraries are ordered; out-of-batch deps do not appear in the result."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {"deps": ["vivarium-c"]},
                "c": {},
            }
        )
        libs = load_libs(libs_dir)
        ordered = sort_topologically(["a", "b"], libs)
        assert "c" not in ordered
        assert ordered.index("b") < ordered.index("a")

    def test_raises_on_cycle(self, make_monorepo: MonorepoFactory) -> None:
        """A dependency cycle among the named libraries raises DependencyCycleError."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"]},
                "b": {"deps": ["vivarium-a"]},
            }
        )
        libs = load_libs(libs_dir)
        with pytest.raises(DependencyCycleError):
            sort_topologically(["a", "b"], libs)


class TestGetReleaseMatrix:
    """Tests for ``get_release_matrix``."""

    def test_include_ordered_dependencies_first(
        self,
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

    def test_wait_for_lists_in_batch_upstreams(
        self,
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

    def test_entry_carries_dist_name_which_need_not_be_vivarium_prefixed(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """Each entry carries its PyPI dist name (the release workflow's git tag
        prefix), which need not be ``vivarium-<dir>`` - e.g. the ``pytest-vivarium``
        plugin, whose dir is ``pytest-vivarium`` and dist is also ``pytest-vivarium``."""
        libs_dir = make_monorepo(
            {
                "config-tree": {"version": "1.0.0"},
                "pytest-vivarium": {
                    "dist_name": "pytest-vivarium",
                    "deps": ["vivarium-config-tree"],
                    "version": "0.1.0",
                },
            }
        )
        libs = load_libs(libs_dir)
        matrix = get_release_matrix(
            {"config-tree": "1.0.0", "pytest-vivarium": "0.1.0"}, libs
        )
        entries = {e["library"]: e for e in matrix["include"]}
        assert entries["config-tree"]["dist"] == "vivarium-config-tree"
        assert entries["pytest-vivarium"]["dist"] == "pytest-vivarium"

    def test_wait_for_empty_for_independent_library(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A library with no in-batch upstream has an empty wait_for."""
        libs_dir = make_monorepo({"a": {"version": "1.0.0"}, "b": {"version": "2.0.0"}})
        libs = load_libs(libs_dir)
        matrix = get_release_matrix({"a": "1.0.0", "b": "2.0.0"}, libs)
        for entry in matrix["include"]:
            assert entry["wait_for"] == []

    def test_omits_out_of_batch_upstream_from_wait_for(
        self,
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

    def test_empty_pairs_yields_empty_include(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """An empty pairs mapping yields {"include": []}."""
        libs_dir = make_monorepo({"a": {}})
        libs = load_libs(libs_dir)
        assert get_release_matrix({}, libs) == {"include": []}

    def test_wait_for_is_transitive_closure(
        self,
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

    def test_raises_keyerror_for_unknown_library(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """A pairs key that is not a known library raises KeyError."""
        libs_dir = make_monorepo({"a": {}})
        libs = load_libs(libs_dir)
        with pytest.raises(KeyError):
            get_release_matrix({"ghost": "1.0.0"}, libs)


class TestBuildInstallPlan:
    """Tests for ``build_install_plan``."""

    def test_installs_target_editable_with_extras(self) -> None:
        """The plan installs the target editably with its [env_reqs] extra."""
        target = _make_lib("a", path=Path("/repo/libs/a"))
        plan = build_install_plan(target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="")
        argv = list(plan.argv)
        assert "-e" in argv
        joined = " ".join(argv)
        assert f"-e {target.path}[ci_github]" in joined

    def test_installs_each_upstream_editable(self) -> None:
        """Each selected upstream appears as an editable (-e) install of its in-tree path."""
        target = _make_lib("a", path=Path("/repo/libs/a"))
        b_path = Path("/repo/libs/b")
        c_path = Path("/repo/libs/c")
        upstreams = [_make_lib("c", path=c_path), _make_lib("b", path=b_path)]
        plan = build_install_plan(
            target, upstreams, env_reqs="ci_github", ihme_pypi="", uv_flags=""
        )
        joined = " ".join(plan.argv)
        assert str(b_path) in joined
        assert str(c_path) in joined

    def test_is_a_single_uv_invocation(self) -> None:
        """Target and all upstreams are installed in one uv pip install command (no clobber)."""
        target = _make_lib("a")
        upstreams = [_make_lib("b"), _make_lib("c")]
        plan = build_install_plan(
            target, upstreams, env_reqs="ci_github", ihme_pypi="", uv_flags=""
        )
        argv = list(plan.argv)
        assert argv[:3] == ["uv", "pip", "install"]
        # only one install verb in the whole command
        assert argv.count("install") == 1

    def test_sets_pretend_version_per_upstream(self) -> None:
        """env carries SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>=<pending version> for each upstream."""
        target = _make_lib("a")
        upstreams = [
            _make_lib("engine", dist_name="vivarium-engine", version="2.3.4"),
            _make_lib("public-health", dist_name="vivarium-public-health", version="5.6.7"),
        ]
        plan = build_install_plan(
            target, upstreams, env_reqs="ci_github", ihme_pypi="", uv_flags=""
        )
        assert plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_ENGINE"] == "2.3.4"
        assert (
            plan.env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_PUBLIC_HEALTH"] == "5.6.7"
        )

    def test_applies_editable_compat_per_library(self) -> None:
        """Each editable library carries the editable_mode=compat config setting."""
        target = _make_lib("a", dist_name="vivarium-a")
        upstreams = [_make_lib("b", dist_name="vivarium-b")]
        plan = build_install_plan(
            target, upstreams, env_reqs="ci_github", ihme_pypi="", uv_flags=""
        )
        joined = " ".join(plan.argv)
        assert "vivarium-a:editable_mode=compat" in joined
        assert "vivarium-b:editable_mode=compat" in joined

    def test_includes_extra_index_when_ihme_pypi_set(self) -> None:
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

    def test_omits_extra_index_when_ihme_pypi_empty(self) -> None:
        """An empty ihme_pypi produces no extra-index flags."""
        target = _make_lib("a")
        plan = build_install_plan(target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="")
        assert "--extra-index-url" not in plan.argv

    def test_passes_through_uv_flags(self) -> None:
        """uv_flags tokens are appended to argv; a blank uv_flags adds nothing."""
        target = _make_lib("a")
        with_flags = build_install_plan(
            target, [], env_reqs="ci_github", ihme_pypi="", uv_flags="--system --no-cache"
        )
        assert "--system" in with_flags.argv
        assert "--no-cache" in with_flags.argv

        blank = build_install_plan(
            target, [], env_reqs="ci_github", ihme_pypi="", uv_flags=" "
        )
        assert "--system" not in blank.argv
        assert "" not in blank.argv


class TestRunInstall:
    """Tests for ``run_install``."""

    def test_invokes_subprocess_with_cwd_check_and_overlaid_env(
        self,
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
        dependency_graph.run_install(plan, libs_dir)

        assert captured["argv"] == list(plan.argv)
        kwargs = captured["kwargs"]
        assert kwargs["cwd"] == libs_dir
        assert kwargs["check"] is True
        env = kwargs["env"]
        assert env["PRE_EXISTING_KEY"] == "keep-me"
        assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VIVARIUM_A"] == "1.0.0"


class TestCLIInstallEditable:
    """Tests for the ``install-editable`` CLI subcommand."""

    def test_runs_selected_plan(
        self,
        make_monorepo: MonorepoFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`install-editable` builds the plan for the selected upstreams and runs it (runner patched)."""
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

        monkeypatch.setattr(dependency_graph.cli, "run_install", fake_run_install)
        exit_code = main_with(
            [
                "install-editable",
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

    def test_hard_fails_on_conflict(
        self,
        make_monorepo: MonorepoFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`install-editable` exits non-zero (without installing) on a version-pin conflict."""
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

        monkeypatch.setattr(dependency_graph.cli, "run_install", fake_run_install)
        exit_code = main_with(
            [
                "install-editable",
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

    def test_clean_exit_on_unknown_library(
        self, make_monorepo: MonorepoFactory, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`install-editable` for a target not under libs/ exits non-zero with a clear message."""
        libs_dir = make_monorepo({"build-utils": {}, "a": {}})
        rc = main_with(["install-editable", "ghost", "--libs-dir", str(libs_dir)])
        assert rc == 1
        assert "unknown library" in capsys.readouterr().err


class TestCLIBuildReleaseMatrix:
    """Tests for the ``build-release-matrix`` CLI subcommand."""

    def test_emits_ordered_json(
        self,
        make_monorepo: MonorepoFactory,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """`build-release-matrix` prints dependency-ordered matrix JSON to stdout."""
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
                "build-release-matrix",
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

    def test_empty_when_no_pairs(
        self,
        make_monorepo: MonorepoFactory,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """`build-release-matrix` with no pairs prints {"include": []} and exits 0."""
        libs_dir = make_monorepo({"a": {}})
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("")
        exit_code = main_with(
            [
                "build-release-matrix",
                "--versions",
                str(pairs_file),
                "--libs-dir",
                str(libs_dir),
            ]
        )
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out) == {"include": []}

    def test_errors_on_missing_version(
        self,
        make_monorepo: MonorepoFactory,
        tmp_path: Path,
    ) -> None:
        """`build-release-matrix` exits non-zero when a pairs line has no version."""
        libs_dir = make_monorepo({"a": {}})
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("a\n")  # no version provided!
        exit_code = main_with(
            [
                "build-release-matrix",
                "--versions",
                str(pairs_file),
                "--libs-dir",
                str(libs_dir),
            ]
        )
        assert exit_code != 0

    def test_clean_exit_on_cycle(
        self,
        make_monorepo: MonorepoFactory,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """`build-release-matrix` on a cyclic batch exits non-zero with a clean message (no traceback)."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b"], "version": "1.0.0"},
                "b": {"deps": ["vivarium-a"], "version": "2.0.0"},
            }
        )
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("a 1.0.0\nb 2.0.0\n")
        exit_code = main_with(
            [
                "build-release-matrix",
                "--versions",
                str(pairs_file),
                "--libs-dir",
                str(libs_dir),
            ]
        )
        assert exit_code != 0
        captured = capsys.readouterr()
        assert "Traceback" not in (captured.out + captured.err)

    def test_orders_over_runtime_deps_only(
        self,
        make_monorepo: MonorepoFactory,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Release ordering uses runtime deps, so a test-extra cycle does not break it.

        Mirrors config-tree <-> testing-utils: a depends on b only via a test extra
        (in ci_github), while b depends on a at runtime. The ci_github graph cycles;
        the runtime graph (b -> a) is a DAG, so the matrix orders a before b.
        """
        libs_dir = make_monorepo(
            {
                "a": {"version": "1.0.0", "extras": {"ci_github": ["vivarium-b"]}},
                "b": {"version": "2.0.0", "deps": ["vivarium-a"]},
            }
        )
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("a 1.0.0\nb 2.0.0\n")
        exit_code = main_with(
            [
                "build-release-matrix",
                "--versions",
                str(pairs_file),
                "--libs-dir",
                str(libs_dir),
            ]
        )
        assert exit_code == 0
        matrix = json.loads(capsys.readouterr().out)
        include = matrix["include"]
        assert [entry["library"] for entry in include] == ["a", "b"]
        a_entry = next(e for e in include if e["library"] == "a")
        b_entry = next(e for e in include if e["library"] == "b")
        assert a_entry["wait_for"] == []
        assert [w["dist"] for w in b_entry["wait_for"]] == ["vivarium-a"]

    def test_clean_exit_on_unknown_library(
        self,
        make_monorepo: MonorepoFactory,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`build-release-matrix` with a pairs entry not under libs/ exits non-zero with a clear message."""
        libs_dir = make_monorepo({"build-utils": {}, "a": {}})
        pairs = tmp_path / "release_pairs.txt"
        pairs.write_text("ghost 1.0.0\n")  # non-monorepo library!
        rc = main_with(
            ["build-release-matrix", "--versions", str(pairs), "--libs-dir", str(libs_dir)]
        )
        assert rc == 1
        assert "unknown library" in capsys.readouterr().err


class TestCLIVerifyEditable:
    """Tests for the ``verify-editable`` CLI subcommand."""

    def test_passes_when_upstreams_editable(
        self, make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`verify-editable` exits 0 when each selected upstream is an editable install."""
        libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
        monkeypatch.setattr(dependency_graph.cli, "_is_editable_install", lambda dist: True)
        rc = main_with(
            ["verify-editable", "a", "--changed", "b", "--libs-dir", str(libs_dir)]
        )
        assert rc == 0

    def test_fails_when_upstream_from_pypi(
        self,
        make_monorepo: MonorepoFactory,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`verify-editable` exits non-zero when a selected upstream is not an editable install."""
        libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
        monkeypatch.setattr(dependency_graph.cli, "_is_editable_install", lambda dist: False)
        rc = main_with(
            ["verify-editable", "a", "--changed", "b", "--libs-dir", str(libs_dir)]
        )
        assert rc == 1
        assert "not editable" in capsys.readouterr().err

    def test_noop_when_no_upstreams(
        self, make_monorepo: MonorepoFactory, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`verify-editable` exits 0 and checks nothing when the target has no changed upstreams."""
        libs_dir = make_monorepo({"a": {}, "b": {}})
        rc = main_with(["verify-editable", "a", "--changed", "", "--libs-dir", str(libs_dir)])
        assert rc == 0
        assert "no changed in-tree upstreams" in capsys.readouterr().out


class TestCLICheckAcyclic:
    """Tests for the ``check-acyclic`` CLI subcommand."""

    def test_passes_on_dag(self, make_monorepo: MonorepoFactory) -> None:
        """`check-acyclic` exits 0 when the in-tree graph is a DAG."""
        libs_dir = make_monorepo({"a": {"deps": ["vivarium-b"]}, "b": {}})
        assert main_with(["check-acyclic", "--libs-dir", str(libs_dir)]) == 0

    def test_fails_on_cycle(
        self, make_monorepo: MonorepoFactory, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`check-acyclic` exits non-zero with a clean message (no traceback) on a cycle."""
        libs_dir = make_monorepo(
            {"a": {"deps": ["vivarium-b"]}, "b": {"deps": ["vivarium-a"]}}
        )
        exit_code = main_with(["check-acyclic", "--libs-dir", str(libs_dir)])
        assert exit_code != 0
        captured = capsys.readouterr()
        assert "Traceback" not in (captured.out + captured.err)


class TestEndToEnd:
    """End-to-end tests across the full install flow."""

    def test_changed_upstream_bump_installs_editable_before_dependent(
        self,
        make_monorepo: MonorepoFactory,
    ) -> None:
        """For target A depending on changed+bumped B: the plan installs B editably at its pending
        version alongside A in a single uv invocation - the core cross-library PR scenario."""
        libs_dir = make_monorepo(
            {
                "a": {"deps": ["vivarium-b>=2.0.0"], "version": "1.0.0"},
                "b": {"version": "2.0.0"},
            }
        )
        libs = load_libs(libs_dir)
        upstreams = get_editable_upstreams("a", libs, changed=["b"])
        plan = build_install_plan(
            libs["a"], upstreams, env_reqs="ci_github", ihme_pypi="", uv_flags=""
        )
        argv = list(plan.argv)
        # Single uv invocation: target and upstream resolved together (no clobber);
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


class TestDiscoverLibsDir:
    """Tests for ``_discover_libs_dir``."""

    def test_from_library_subdir(
        self, make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-discovery walks up from a library subdir to the libs/ dir holding build-utils."""
        libs_dir = make_monorepo(
            {"build-utils": {}, "engine": {"deps": ["vivarium-build-utils"]}}
        )
        monkeypatch.chdir(libs_dir / "engine")
        assert dependency_graph._discover_libs_dir(None) == libs_dir

    def test_from_repo_root(
        self, make_monorepo: MonorepoFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-discovery finds libs/ when run from the repo root (libs/'s parent)."""
        libs_dir = make_monorepo({"build-utils": {}})
        monkeypatch.chdir(libs_dir.parent)
        assert dependency_graph._discover_libs_dir(None) == libs_dir

    def test_falls_back_to_cwd_libs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no build-utils library anywhere above, discovery falls back to <cwd>/libs."""
        monkeypatch.chdir(tmp_path)
        assert dependency_graph._discover_libs_dir(None) == tmp_path / "libs"

    def test_honors_path_provided(self, tmp_path: Path) -> None:
        """A provided path is used verbatim (resolved), without walking."""
        assert dependency_graph._discover_libs_dir(str(tmp_path)) == tmp_path.resolve()
