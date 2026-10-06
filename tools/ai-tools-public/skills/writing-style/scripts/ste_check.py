#!/usr/bin/env python3
"""Check prose against a subset of the writing-style rules.

The rules are based on ASD-STE100 Simplified Technical English. ASD-STE100 is a
copyright and trademark of ASD, Brussels. This script is not an ASD tool and a
clean result does not mean that the text complies with the standard.

Hard findings have a very low false-positive rate and make the script exit 1.
Advisory findings need the writer's judgment and never change the exit code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

CODE_TOKEN = "CODE"
URL_TOKEN = "URL"

MAX_WORDS_HARD = 35
MAX_WORDS_DESCRIPTIVE = 25
MAX_WORDS_PROCEDURE = 20
MAX_SENTENCES_HARD = 8
MAX_SENTENCES_ADVISORY = 6

CONTRACTION = re.compile(
    r"\b(?:\w+n['’]t|\w+['’](?:re|ve|ll|m|d)|(?:it|that|there|what|here|let|who|where)['’]s)\b",
    re.IGNORECASE,
)
LATIN = re.compile(r"(?<!\w)(?:e\.g\.|i\.e\.|etc\.|viz\.|cf\.)", re.IGNORECASE)
IRREGULAR_PARTICIPLES = (
    "been|done|made|written|run|built|seen|found|given|taken|known|shown|chosen|"
    "broken|hidden|kept|left|lost|meant|put|read|sent|set|split|spent|told|thrown|"
    "understood|won|begun|become|brought|bought|caught|drawn|driven|fallen|got|gotten|"
    "grown|held|led|paid|said|sold|struck|stuck|taught|thought|woken|worn"
)
PRESENT_PERFECT = re.compile(
    rf"\b(?:has|have|had|hasn['’]t|haven['’]t)\s+(?:\w+ly\s+)?(?:\w+ed|{IRREGULAR_PARTICIPLES})\b",
    re.IGNORECASE,
)
PASSIVE_EXCEPTIONS = {
    "based",
    "located",
    "supposed",
    "interested",
    "concerned",
    "related",
    "involved",
    "aligned",
    "tired",
    "pleased",
}
PASSIVE = re.compile(
    rf"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w+ed|{IRREGULAR_PARTICIPLES})\b",
    re.IGNORECASE,
)
PROGRESSIVE_EXCEPTIONS = {
    "missing",
    "willing",
    "pending",
    "outstanding",
    "ongoing",
    "interesting",
    "existing",
    "remaining",
    "matching",
    "failing",
    "passing",
}
PROGRESSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been)\s+(?!going\b)(?!(?:no|some|any|every)thing\b)(\w{3,}ing)\b",
    re.IGNORECASE,
)
PHRASAL_VERBS = re.compile(
    r"\b(?:"
    r"set(?:s|ting)? up|find(?:s|ing)? out|found out|figur(?:e|es|ed|ing) out|"
    r"carr(?:y|ies|ied|ying) out|look(?:s|ed|ing)? into|point(?:s|ed|ing)? out|"
    r"come up with|end(?:s|ed|ing)? up|go(?:es|ing)? through|went through|"
    r"check(?:s|ed|ing)? out|fill(?:s|ed|ing)? (?:in|out)|pick(?:s|ed|ing)? up|"
    r"turn(?:s|ed|ing)? (?:on|off)|break(?:s|ing)? down|broke down|"
    r"r[au]n(?:s|ning)? into|sort(?:s|ed|ing)? out|get(?:s|ting)? rid of|got rid of|"
    r"put(?:s|ting)? together|look(?:s|ed|ing)? up|leav(?:e|es|ing) out|left out|"
    r"giv(?:e|es|ing) up|gave up|deal(?:s|t|ing)? with|mak(?:e|es|ing) up|made up|"
    r"bring(?:s|ing)? up|brought up|take(?:s|n)? care of|took care of"
    r")\b",
    re.IGNORECASE,
)
FIGURATIVE = re.compile(
    r"\b(?:under the hood|workhorse|killer feature|game[- ]changer|deep dive|"
    r"silver bullet|low-hanging fruit|heavy lifting|not just|not only)\b",
    re.IGNORECASE,
)
SENTENCE_START_CONJUNCTION = re.compile(r"^(?:And|But|So)\b")
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")

FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
URL = re.compile(r"https?://[^\s<>]*[^\s<>.,;:!?)\]'\"]")
HTML_ENTITY = re.compile(r"&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")
MARKDOWN_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?")
MARKDOWN_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
MARKDOWN_STRUCTURE = re.compile(r"^(#{1,6}\s|>|\||[-=*_]{3,}\s*$)")
WIKI_LIST = re.compile(r"^\s*[*#-]+\s+")
WIKI_NUMBERED = re.compile(r"^\s*#+\s+")
WIKI_BLOCK = re.compile(r"\{(code|noformat|quote)(?::[^}]*)?\}", re.IGNORECASE)
WIKI_INLINE_CODE = re.compile(r"\{\{.*?\}\}")
WIKI_STRUCTURE = re.compile(r"^(h[1-6]\.\s|bq\.|\|\||\||----\s*$)")
RST_UNDERLINE = re.compile(r"^([-=~^*#+`'\"])\1{2,}\s*$")
SENTENCE_END = re.compile(r"[.!?][\"'”’)\]*_]*\s+[\"'“‘(\[*_]*(\S)")
ABBREVIATIONS = {"fig", "figs", "dr", "mr", "mrs", "ms", "no", "vs", "approx", "eq", "sec"}
ABBREVIATIONS |= {"vol", "e.g", "i.e", "cf", "al", "viz"}


@dataclass
class Finding:
    """One rule violation at a line of the input."""

    line: int
    severity: str
    rule: str
    message: str
    excerpt: str


@dataclass
class Unit:
    """A paragraph or list item, with the input line where each character came from."""

    text: str
    lines: list[int]
    procedure: bool = False


@dataclass
class Report:
    """The findings and summary metrics for one input."""

    findings: list[Finding] = field(default_factory=list)
    sentences: int = 0
    words: int = 0
    long_sentences: int = 0

    @property
    def hard(self) -> list[Finding]:
        """Return the hard findings."""
        return [f for f in self.findings if f.severity == "hard"]

    def summary(self) -> dict[str, float]:
        """Return the summary metrics."""
        mean = round(self.words / self.sentences, 1) if self.sentences else 0.0
        return {
            "sentences": self.sentences,
            "mean_words_per_sentence": mean,
            "sentences_over_25_words": self.long_sentences,
            "hard": len(self.hard),
            "advisory": len(self.findings) - len(self.hard),
        }

    def to_dict(self) -> dict[str, object]:
        """Return the report as a JSON-ready dictionary."""
        return {"summary": self.summary(), "findings": [asdict(f) for f in self.findings]}


def _strip_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove the HTML comment text from one line, given whether a comment is open."""
    kept = ""
    while line:
        if in_comment:
            close = line.find("-->")
            if close < 0:
                return kept, True
            line, in_comment = line[close + 3 :], False
        else:
            opening = line.find("<!--")
            if opening < 0:
                return kept + line, False
            kept, line, in_comment = kept + line[:opening], line[opening + 4 :], True
    return kept, in_comment


def strip_out_of_scope(
    text: str, kind: str, skip_patterns: list[re.Pattern[str]]
) -> list[str]:
    """Blank or replace the text that the rules do not govern, keeping the line count.

    Parameters
    ----------
    text
        The raw input.
    kind
        The markup of the input: ``markdown``, ``wiki``, or ``plain``.
    skip_patterns
        Extra patterns. A line that matches one of them is blanked.

    Returns
    -------
        One string per input line. An excluded line becomes an empty string.
    """
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    fence: str | None = None
    wiki_block: str | None = None
    in_comment = False
    in_directive = False
    front_matter = kind == "markdown" and raw_lines[0].strip() == "---"
    for number, line in enumerate(raw_lines):
        if front_matter:
            out.append("")
            front_matter = number == 0 or line.strip() not in ("---", "...")
            continue
        if kind == "wiki":
            line = WIKI_INLINE_CODE.sub(CODE_TOKEN, line)
            markers = WIKI_BLOCK.findall(line)
            if wiki_block or markers:
                for marker in (m.lower() for m in markers):
                    wiki_block = None if wiki_block == marker else (wiki_block or marker)
                out.append("")
                continue
        else:
            match = FENCE.match(line)
            if fence:
                closes = (
                    match is not None
                    and match.group(1)[0] == fence[0]
                    and len(match.group(1)) >= len(fence)
                    and not match.group(2).strip()
                )
                fence = None if closes else fence
                out.append("")
                continue
            # A backtick fence whose info string has a backtick is inline code, not a fence.
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = match.group(1)
                out.append("")
                continue
            line = INLINE_CODE.sub(CODE_TOKEN, line)
            if kind == "markdown":
                line, in_comment = _strip_comments(line, in_comment)
        stripped = line.strip()
        if kind == "plain":
            # Commit bodies and RST files: skip directives, tables, and literal blocks.
            if in_directive and (not stripped or line[:1].isspace()):
                out.append("")
                continue
            in_directive = stripped.startswith(".. ")
            indented_block = line.startswith("    ") and (not out or not out[-1].strip())
            if in_directive or "\t" in line or indented_block:
                out.append("")
                continue
            if RST_UNDERLINE.match(stripped):
                if out:
                    out[-1] = ""
                out.append("")
                continue
        if any(p.search(line) for p in skip_patterns):
            out.append("")
            continue
        structure = WIKI_STRUCTURE if kind == "wiki" else MARKDOWN_STRUCTURE
        if kind != "plain" and structure.match(stripped):
            out.append("")
            continue
        if kind == "wiki":
            line = re.sub(r"\[([^|\]]*)\|[^\]]*\]", r"\1", line)
        else:
            line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
            line = HTML_ENTITY.sub(" ", line)
        out.append(URL.sub(URL_TOKEN, line))
    return out


def split_units(lines: list[str], kind: str) -> list[Unit]:
    """Group the cleaned lines into paragraphs and list items."""
    list_marker = WIKI_LIST if kind == "wiki" else MARKDOWN_LIST
    numbered = WIKI_NUMBERED if kind == "wiki" else MARKDOWN_NUMBERED
    units: list[Unit] = []
    current: Unit | None = None
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            current = None
            continue
        marker = list_marker.match(line)
        if marker:
            body = line[marker.end() :]
            current = Unit(text="", lines=[], procedure=bool(numbered.match(line)))
            units.append(current)
        else:
            body = line.strip()
            if current is None:
                current = Unit(text="", lines=[])
                units.append(current)
        if current.text:
            current.text += " "
            current.lines.append(number)
        current.text += body
        current.lines.extend([number] * len(body))
    return units


def _is_sentence_end(text: str, match: re.Match[str], quotes: list[tuple[int, int]]) -> bool:
    """Return whether a candidate break is a real sentence end."""
    if any(start < match.start() and end > match.start(1) for start, end in quotes):
        return False
    before = text[: match.start()].split()
    token = before[-1].lower().lstrip("(\"'“*_") if before else ""
    if token in ABBREVIATIONS:
        return False
    return not (token == "etc" and match.group(1).islower())


def split_sentences(unit: Unit) -> list[tuple[str, int]]:
    """Split a unit into sentences, each paired with the line where it starts."""
    quotes = [m.span() for m in QUOTED.finditer(unit.text)]
    sentences: list[tuple[str, int]] = []
    start = 0
    for match in SENTENCE_END.finditer(unit.text):
        if not _is_sentence_end(unit.text, match, quotes):
            continue
        boundary = match.start(1)
        while boundary > match.start() and not unit.text[boundary - 1].isspace():
            boundary -= 1
        sentences.append((unit.text[start:boundary].strip(), unit.lines[start]))
        start = boundary
    if unit.text[start:].strip():
        sentences.append((unit.text[start:].strip(), unit.lines[start]))
    return [(s, n) for s, n in sentences if s]


def count_words(sentence: str) -> int:
    """Count words, with each parenthetical and each hyphenated word as one word."""
    collapsed = re.sub(r"\([^()]*\)", " PAREN ", sentence)
    return len([t for t in collapsed.split() if re.search(r"[A-Za-z0-9]", t)])


def excerpt(sentence: str, limit: int = 80) -> str:
    """Return the start of a sentence for display."""
    return sentence if len(sentence) <= limit else sentence[: limit - 3] + "..."


def check_sentence(sentence: str, line: int, procedure: bool, report: Report) -> None:
    """Add the findings for one sentence to the report."""
    words = count_words(sentence)
    report.sentences += 1
    report.words += words
    if words > MAX_WORDS_DESCRIPTIVE:
        report.long_sentences += 1

    def add(severity: str, rule: str, message: str) -> None:
        report.findings.append(Finding(line, severity, rule, message, excerpt(sentence)))

    limit = MAX_WORDS_PROCEDURE if procedure else MAX_WORDS_DESCRIPTIVE
    # Quoted words belong to someone else, so only the length rules see them.
    checked = QUOTED.sub("QUOTE", sentence)
    if words > MAX_WORDS_HARD:
        add(
            "hard",
            "sentence-length",
            f"{words} words, over the hard limit of {MAX_WORDS_HARD}",
        )
    elif words > limit:
        kind = "procedure step" if procedure else "sentence"
        add("advisory", "sentence-length", f"{words} words, over {limit} for a {kind}")
    if ";" in checked:
        add("hard", "semicolon", "use two sentences or a list instead of a semicolon")
    for match in CONTRACTION.finditer(checked):
        add("hard", "contraction", f"write {match.group(0)!r} in full")
    for match in LATIN.finditer(checked):
        add("hard", "latin-abbreviation", f"replace {match.group(0)!r} with plain words")
    for match in PRESENT_PERFECT.finditer(checked):
        add(
            "advisory",
            "present-perfect",
            f"{match.group(0)!r}: use the simple past or present",
        )
    for match in PASSIVE.finditer(checked):
        if match.group(1).lower() not in PASSIVE_EXCEPTIONS:
            add(
                "advisory",
                "passive-voice",
                f"{match.group(0)!r}: name the actor if it is known",
            )
    for match in PROGRESSIVE.finditer(checked):
        if match.group(1).lower() not in PROGRESSIVE_EXCEPTIONS:
            add("advisory", "ing-verb", f"{match.group(0)!r}: use a simple tense")
    for match in PHRASAL_VERBS.finditer(checked):
        add("advisory", "phrasal-verb", f"{match.group(0)!r}: use a one-word verb")
    for match in FIGURATIVE.finditer(checked):
        add("advisory", "figurative", f"{match.group(0)!r}: say it literally")
    if SENTENCE_START_CONJUNCTION.match(checked):
        add(
            "advisory",
            "opening-conjunction",
            "join to the previous sentence or drop the word",
        )


def check(
    text: str, kind: str = "markdown", skip_patterns: list[str] | None = None
) -> Report:
    """Check prose and return the findings.

    Parameters
    ----------
    text
        The text to check.
    kind
        The markup of the text: ``markdown``, ``wiki``, or ``plain``.
    skip_patterns
        Regular expressions. Lines that match one of them are not checked.

    Returns
    -------
        The findings and summary metrics.
    """
    patterns = [re.compile(p) for p in skip_patterns or []]
    report = Report()
    for unit in split_units(strip_out_of_scope(text, kind, patterns), kind):
        sentences = split_sentences(unit)
        for sentence, line in sentences:
            check_sentence(sentence, line, unit.procedure, report)
        if len(sentences) > MAX_SENTENCES_HARD:
            severity, limit = "hard", MAX_SENTENCES_HARD
        elif len(sentences) > MAX_SENTENCES_ADVISORY:
            severity, limit = "advisory", MAX_SENTENCES_ADVISORY
        else:
            continue
        report.findings.append(
            Finding(
                unit.lines[0],
                severity,
                "paragraph-length",
                f"{len(sentences)} sentences, over {limit} in one paragraph",
                excerpt(sentences[0][0]),
            )
        )
    report.findings.sort(key=lambda f: (f.line, f.severity != "hard"))
    return report


def format_text(report: Report) -> str:
    """Format a report for a person to read."""
    out = [
        f"line {f.line}: [{f.severity}] {f.rule}: {f.message}\n    {f.excerpt}"
        for f in report.findings
    ]
    summary = report.summary()
    out.append(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in summary.items()))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Run the checker from the command line and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", nargs="?", help="file to check (default: stdin)")
    parser.add_argument("--kind", choices=["markdown", "wiki", "plain"], default="markdown")
    parser.add_argument(
        "--skip-pattern",
        action="append",
        default=[],
        help="regex for lines to leave unchecked, such as a template line (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 0 if error.code == 0 else 2
    try:
        data = Path(args.path).read_bytes() if args.path else sys.stdin.buffer.read()
        report = check(data.decode("utf-8"), args.kind, args.skip_pattern)
    except (OSError, UnicodeDecodeError, re.error) as error:
        print(f"ste_check: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2) if args.json else format_text(report))
    return 1 if report.hard else 0


if __name__ == "__main__":
    sys.exit(main())
