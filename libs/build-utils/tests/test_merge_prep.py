"""Tests for the merge-prep helpers (``vivarium.build_utils.merge_prep``)."""

from __future__ import annotations

from vivarium.build_utils.merge_prep import should_squash, update_changelog_date


class TestShouldSquash:
    """Tests for ``should_squash``."""

    def test_feature_branch_is_squashed(self) -> None:
        """A normal feature branch (no protected prefix) is squashed."""
        assert should_squash("albrja/build-utils/mic-1234/some-feature") is True

    def test_epic_branch_is_preserved(self) -> None:
        """An ``epic/`` branch keeps its per-task commits."""
        assert should_squash("epic/some-epic") is False

    def test_release_candidate_variants_are_preserved(self) -> None:
        """Both ``release-candidate/`` and ``release_candidate/`` prefixes are preserved."""
        assert should_squash("release-candidate/1.2.3") is False
        assert should_squash("release_candidate/1.2.3") is False


class TestUpdateChangelogDate:
    """Tests for ``update_changelog_date``."""

    def test_replaces_an_existing_date(self) -> None:
        """The first-line date is rewritten; the version and body are untouched."""
        content = "**4.2.13 - 07/22/26**\n\n- A change\n"
        assert (
            update_changelog_date(content, "07/25/26")
            == "**4.2.13 - 07/25/26**\n\n- A change\n"
        )

    def test_replaces_a_tbd_placeholder(self) -> None:
        """A ``TBD/TBD/2026`` placeholder date is replaced with today."""
        content = "**4.5.0 - TBD/TBD/2026**\n\n- A change\n"
        assert (
            update_changelog_date(content, "07/25/26")
            == "**4.5.0 - 07/25/26**\n\n- A change\n"
        )

    def test_preserves_the_rest_of_the_file(self) -> None:
        """Only the first line changes; later entries are left alone."""
        content = "**1.0.0 - 01/02/26**\n\n- one\n- two\n\n**0.9.0 - 01/01/26**\n\n- old\n"
        assert update_changelog_date(content, "07/25/26") == (
            "**1.0.0 - 07/25/26**\n\n- one\n- two\n\n**0.9.0 - 01/01/26**\n\n- old\n"
        )

    def test_leaves_a_non_heading_first_line_unchanged(self) -> None:
        """A first line that is not a version heading is returned untouched."""
        content = "Not a release heading\n\n- x\n"
        assert update_changelog_date(content, "07/25/26") == content

    def test_handles_empty_content(self) -> None:
        """Empty content is returned as-is."""
        assert update_changelog_date("", "07/25/26") == ""
