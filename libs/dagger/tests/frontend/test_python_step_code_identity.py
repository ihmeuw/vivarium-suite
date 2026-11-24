"""Tests of the code identity that a python step gets, and of builds that use it."""

from pathlib import Path

import pytest

# Wiring


def test_a_pipeline_resolves_and_exposes_its_repository_root(tmp_path: Path) -> None:
    """Pipeline(repository_root=<relative path>) exposes the resolved path as repository_root, and a pipeline made without it exposes None."""


def test_a_python_step_gets_a_code_identity_node_that_starts_at_its_targets_module(
    tmp_path: Path,
) -> None:
    """A step added with add_python_step, with one output key and with a mapping of outputs, has a CodeIdentityNode as its task's code_id, named "<step>::code", whose start is the resolved file of the target's module and whose repository root is the pipeline's."""


def test_a_target_without_a_source_file_gets_an_always_stale_identity(tmp_path: Path) -> None:
    """A python step whose target's module has no source file (a function whose __module__ names a module without __file__) gets an always-stale code identity named "<step>::code"."""


@pytest.mark.parametrize("kind", ["command", "notebook", "simulation"])
def test_other_kinds_of_step_keep_an_always_stale_identity(tmp_path: Path, kind: str) -> None:
    """A command step, a notebook step and a simulation step still get an always-stale code identity."""


# Builds


def test_a_second_build_with_no_edit_skips_every_python_step(tmp_path: Path) -> None:
    """In process, a pipeline of two chained python steps whose functions live in a module under the declared repository root runs both steps on its first build and skips both on a second build with no edit, and the preview before the second build lists no step as would run."""


def test_a_second_build_through_the_bridge_with_no_edit_submits_nothing(
    tmp_path: Path,
) -> None:
    """Through the bridge with the Jobmon test double, the same pipeline binds both steps on its first build and binds no workflow on a second build with no edit."""


def test_an_edit_to_a_helper_reruns_only_the_steps_that_import_it(tmp_path: Path) -> None:
    """With three python steps whose functions are in two modules, of which one imports a helper module directly and another through a second module, a code edit to the helper re-runs exactly the steps whose code imports it and skips the third."""


def test_a_comment_edit_reruns_nothing(tmp_path: Path) -> None:
    """A comment added to a module that every step imports re-runs no step on the next build."""


def test_without_a_repository_root_a_step_in_the_repository_reruns_every_build(
    tmp_path: Path,
) -> None:
    """A pipeline made without repository_root, whose step functions live in an ordinary directory that is not an editable install, runs the steps again on a second build with no edit."""
