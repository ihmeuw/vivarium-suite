from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str) -> ModuleType:
    """Import a script from the skill's scripts directory."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ste_check = _load("ste_check")
check_agent_blocks = _load("check_agent_blocks")


def _rules(text: str, kind: str = "markdown", severity: str | None = None) -> list[str]:
    """Return the rule names found in the text."""
    report = ste_check.check(text, kind)
    return [f.rule for f in report.findings if severity is None or f.severity == severity]


def _sentence(words: int) -> str:
    """Return a sentence with the given number of words."""
    return " ".join(["word"] * (words - 1) + ["end."])


@pytest.mark.parametrize(
    "text, rule",
    [
        ("The loader reads the file; the parser checks it.", "semicolon"),
        ("The loader doesn't read the file.", "contraction"),
        ("It's a cache.", "contraction"),
        ("Use a short name, e.g. the stem.", "latin-abbreviation"),
        ("Remove the files, the logs, etc. from the directory.", "latin-abbreviation"),
        (_sentence(36), "sentence-length"),
    ],
)
def test_hard_findings(text: str, rule: str) -> None:
    assert rule in _rules(text, severity="hard")


@pytest.mark.parametrize(
    "text, rule",
    [
        ("This PR has added a cache.", "present-perfect"),
        ("The parser is called twice.", "passive-voice"),
        ("The loader was causing the error.", "ing-verb"),
        ("We set up the environment.", "phrasal-verb"),
        ("The cache is the workhorse of the loader.", "figurative"),
        ("The test passed. But the build failed.", "opening-conjunction"),
        (_sentence(26), "sentence-length"),
    ],
)
def test_advisory_findings(text: str, rule: str) -> None:
    assert rule in _rules(text, severity="advisory")


@pytest.mark.parametrize(
    "text",
    [
        "The user's config file sets the path.",
        "The rules are based on the standard.",
        "Logging and caching are technical nouns.",
        "The loader reads the file. The parser checks the result.",
        'Write "this PR adds", not "this PR has added".',
    ],
)
def test_clean_text_has_no_findings(text: str) -> None:
    assert _rules(text) == []


def test_code_is_out_of_scope() -> None:
    text = (
        "Run `a; b` first.\n\n```python\nx = 1; y = 2  # isn't\n```\n\nThe loader reads it.\n"
    )
    assert _rules(text) == []


def test_markdown_structure_is_out_of_scope() -> None:
    text = (
        "## Heading; with a semicolon\n"
        "<!-- a comment; with e.g. a semicolon\nover two lines -->\n"
        "| col; one | col two |\n"
        "> a quote; from a log\n"
        "See [the docs page](https://example.com/a;b) and https://example.com/c;d.\n"
    )
    assert _rules(text) == []


def test_wiki_markup_is_out_of_scope() -> None:
    text = (
        "h2. Background; notes\n"
        "{code:python}\nx = 1; y = 2\n{code}\n"
        "{noformat}\nError; it's broken\n{noformat}\n"
        "Call {{a; b}} once.\n"
    )
    assert _rules(text, kind="wiki") == []


def test_skip_pattern_blanks_matching_lines() -> None:
    text = "TL;DR: _TODO_\n\nThe loader reads the file.\n"
    report = ste_check.check(text, "markdown", [r"^TL;DR"])
    assert report.findings == []


def test_numbered_list_items_use_the_procedure_limit() -> None:
    step = " ".join(["word"] * 21) + "."
    assert "sentence-length" in _rules(f"1. {step}\n", severity="advisory")
    assert _rules(f"- {step}\n") == []


def test_paragraph_length_limits() -> None:
    def paragraph(n: int) -> str:
        return " ".join(["The loader reads it."] * n)

    assert _rules(paragraph(6)) == []
    assert _rules(paragraph(7), severity="advisory") == ["paragraph-length"]
    assert _rules(paragraph(9), severity="hard") == ["paragraph-length"]


def test_word_count_treats_parentheses_and_code_as_one_word() -> None:
    assert ste_check.count_words("Use CODE (the fast path, not the slow one) here.") == 4


def test_findings_report_the_input_line() -> None:
    text = "The first line is fine.\n\nThe third line isn't fine.\n"
    [finding] = ste_check.check(text).findings
    assert finding.line == 3


def test_repo_pr_template_has_no_hard_findings() -> None:
    template = Path(__file__).resolve().parents[5] / ".github" / "pull_request_template.md"
    if not template.exists():
        pytest.skip("no PR template in this checkout")
    assert ste_check.check(template.read_text()).hard == []


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("The loader reads the file.\n")
    dirty = tmp_path / "dirty.md"
    dirty.write_text("The loader reads the file; it is fine.\n")
    assert ste_check.main([str(clean)]) == 0
    assert ste_check.main([str(dirty), "--json"]) == 1
    assert '"semicolon"' in capsys.readouterr().out
    assert ste_check.main([str(tmp_path / "missing.md")]) == 2
    assert ste_check.main(["--kind", "html"]) == 2


def test_wiki_inline_code_does_not_open_a_block() -> None:
    text = (
        "Use the {{code}} macro for snippets.\n\nThe loader reads it; the parser checks it.\n"
    )
    assert "semicolon" in _rules(text, kind="wiki", severity="hard")


def test_longer_fence_contains_a_shorter_fence() -> None:
    text = "````markdown\n```python\nx = 1; y = 2\n```\n````\n\nThe loader reads it.\n"
    assert _rules(text) == []


def test_single_line_fence_does_not_open_a_block() -> None:
    text = "```inline code```\n\nThe loader reads it; the parser checks it.\n"
    assert "semicolon" in _rules(text, severity="hard")


def test_comment_marker_in_inline_code_does_not_hide_text() -> None:
    text = "Start with `<!--` here.\n\nThe loader reads it; the parser checks it.\n\nEnd with `-->`.\n"
    assert "semicolon" in _rules(text, severity="hard")


def test_front_matter_and_entities_are_out_of_scope() -> None:
    text = "---\nname: x; y\n---\n\nThe loader&nbsp;reads the file.\n"
    assert _rules(text) == []


def test_plain_commit_body_splits_bullets() -> None:
    body = "Add the checker\n\n" + "".join(
        f"- add a script that checks the prose in item {n} of the list\n" for n in range(4)
    )
    assert _rules(body, kind="plain", severity="hard") == []


def test_plain_skips_rst_structure() -> None:
    text = (
        "Heading without a period\n========================\n\n"
        ".. code-block:: python\n\n    x = 1; y = 2\n\n"
        "col one\tcol two; three\n\n"
        "The loader reads the file.\n"
    )
    assert _rules(text, kind="plain") == []


def test_url_keeps_the_sentence_end() -> None:
    report = ste_check.check("See https://example.com/docs. The loader reads it.\n")
    assert report.sentences == 2


@pytest.mark.parametrize(
    "text, sentences",
    [
        ("The check passed. mypy reported nothing.", 2),
        ("**Label.** The loader reads it.", 2),
        ("See Fig. 3 for the result.", 1),
        ("Compare A vs. B first.", 1),
        ("He said “stop now.” The loader stopped.", 2),
    ],
)
def test_sentence_splitting(text: str, sentences: int) -> None:
    assert ste_check.check(text).sentences == sentences


def test_quotation_with_two_sentences_stays_exempt() -> None:
    assert _rules("The log said \"it's broken. Don't retry.\" and stopped.") == []


def test_excerpt_shows_the_original_quoted_text() -> None:
    [finding] = ste_check.check('The "fast" loader is called twice.').findings
    assert '"fast"' in finding.excerpt


@pytest.mark.parametrize(
    "text", ["The flag is missing.", "Nothing is pending.", "It is nothing."]
)
def test_progressive_exceptions(text: str) -> None:
    assert "ing-verb" not in _rules(text)


def test_invalid_utf8_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe bad bytes")
    assert ste_check.main([str(path)]) == 2


def test_agent_block_check(tmp_path: Path) -> None:
    canonical = check_agent_blocks.canonical_line()
    good = tmp_path / "good.md"
    good.write_text(f"## Constraints\n\n{canonical}\n")
    drifted = tmp_path / "drifted.md"
    drifted.write_text(f"{canonical} Extra words.\n")
    missing = tmp_path / "missing.md"
    missing.write_text("## Constraints\n")
    assert check_agent_blocks.problems(good, canonical, required=True) == []
    assert len(check_agent_blocks.problems(drifted, canonical, required=False)) == 1
    assert len(check_agent_blocks.problems(missing, canonical, required=True)) == 1
    assert check_agent_blocks.problems(missing, canonical, required=False) == []


def test_plugin_agents_match_the_canonical_block() -> None:
    assert check_agent_blocks.main([]) == 0


def test_agent_block_check_reports_a_missing_file(tmp_path: Path) -> None:
    assert check_agent_blocks.main([str(tmp_path / "missing.md")]) == 2
