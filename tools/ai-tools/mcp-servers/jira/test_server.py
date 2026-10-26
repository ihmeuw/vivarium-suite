"""Unit tests for the Jira MCP server. Jira is replaced by a fake ``_request``.

Run from this directory with:
    uv run --with pytest --with httpx --with "mcp>=2.3,<3" pytest test_server.py
"""

from typing import Any

import pytest
import server
from mcp.server.mcpserver.exceptions import ToolError

LINK_TYPES = [
    {"name": "Blocks", "outward": "blocks", "inward": "is blocked by"},
    {"name": "Relates", "outward": "related to", "inward": "related to"},
]


class FakeJira:
    """Record requests and answer them from a table of canned responses."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        response = self.responses.get((method, path), {})
        if isinstance(response, Exception):
            raise response
        return response

    def bodies(self, method: str, path: str) -> list[dict[str, Any]]:
        return [kw.get("json") for m, p, kw in self.calls if (m, p) == (method, path)]


@pytest.fixture
def jira(monkeypatch: pytest.MonkeyPatch) -> FakeJira:
    fake = FakeJira({("GET", "/issueLinkType"): {"issueLinkTypes": LINK_TYPES}})
    monkeypatch.setattr(server, "_request", fake)
    return fake


def test_token_read_from_tokens_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setattr(server.Path, "home", lambda: tmp_path)
    (tmp_path / ".tokens").write_text("export OTHER=1\nexport JIRA_API_TOKEN='abc123'\n")
    assert server._token() == "abc123"


def test_token_missing_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setattr(server.Path, "home", lambda: tmp_path)
    with pytest.raises(ToolError, match="JIRA_API_TOKEN is not set"):
        server._token()


@pytest.mark.parametrize(
    "phrase, inward, outward",
    [
        ("blocks", "MIC-1", "MIC-2"),
        ("Blocks", "MIC-1", "MIC-2"),
        ("is blocked by", "MIC-2", "MIC-1"),
        ("related to", "MIC-1", "MIC-2"),
    ],
)
def test_link_direction(jira, phrase, inward, outward) -> None:
    server.link_issues("MIC-1", "MIC-2", phrase)
    (body,) = jira.bodies("POST", "/issueLink")
    assert body["inwardIssue"]["key"] == inward
    assert body["outwardIssue"]["key"] == outward


def test_unknown_link_lists_options(jira) -> None:
    with pytest.raises(ToolError, match="is blocked by"):
        server.link_issues("MIC-1", "MIC-2", "duplicates")


def test_create_issue_sets_new_fields(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "MIC-9"}
    result = server.create_issue(
        summary="s",
        description="d",
        issue_type="Story",
        priority="Major",
        labels=["AI_Tools"],
        fix_versions=["AI"],
        story_points=2.01,
        epic="MIC-100",
        links={"is blocked by": ["MIC-5"]},
    )
    (body,) = jira.bodies("POST", "/issue")
    fields = body["fields"]
    assert fields["project"] == {"key": "MIC"}
    assert fields["priority"] == {"name": "Major"}
    assert fields["fixVersions"] == [{"name": "AI"}]
    assert fields[server.STORY_POINTS_FIELD] == 2.01
    assert fields[server.EPIC_LINK_FIELD] == "MIC-100"
    assert server.EPIC_NAME_FIELD not in fields
    assert result == {
        "key": "MIC-9",
        "url": "https://jira.ihme.washington.edu/browse/MIC-9",
        "sprint_failure": None,
        "link_failures": [],
    }
    (link,) = jira.bodies("POST", "/issueLink")
    assert (link["inwardIssue"]["key"], link["outwardIssue"]["key"]) == ("MIC-5", "MIC-9")


def test_create_epic_defaults_epic_name(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "MIC-9"}
    server.create_issue(summary="Big work", description="d", issue_type="Epic")
    (body,) = jira.bodies("POST", "/issue")
    assert body["fields"][server.EPIC_NAME_FIELD] == "Big work"


def test_create_issue_reports_sprint_failure(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "MIC-9"}
    jira.responses[("GET", f"/board/{server.BOARD_ID}/sprint")] = {"values": []}
    result = server.create_issue(summary="s", description="d", sprint="active")
    assert result["key"] == "MIC-9"
    assert "found 0" in result["sprint_failure"]


def test_sprint_by_name(jira) -> None:
    jira.responses[("GET", f"/board/{server.BOARD_ID}/sprint")] = {
        "values": [{"id": 1, "name": "Vivarium Sprint 199"}, {"id": 2, "name": "Next"}],
        "isLast": True,
    }
    assert server._resolve_sprint("Next") == 2
    with pytest.raises(ToolError, match="No active or future sprint"):
        server._resolve_sprint("Missing")


def test_update_moves_to_backlog(jira) -> None:
    result = server.update_issue("MIC-1", sprint="Backlog")
    assert result["sprint_failure"] is None
    assert jira.calls == [
        ("POST", "/backlog/issue", {"json": {"issues": ["MIC-1"]}, "base": server.AGILE_URL})
    ]


def test_update_refuses_dropped_macro(jira) -> None:
    jira.responses[("GET", "/issue/MIC-1")] = {
        "fields": {"description": "text {code:python}x = 1{code} {panel}p{panel}"}
    }
    with pytest.raises(ToolError, match=r"\['code'\]"):
        server.update_issue("MIC-1", description="{panel}p{panel} only")
    assert not jira.bodies("PUT", "/issue/MIC-1")


def test_update_force_overwrites(jira) -> None:
    server.update_issue("MIC-1", description="plain", force=True)
    assert jira.bodies("PUT", "/issue/MIC-1") == [{"fields": {"description": "plain"}}]


def test_update_clears_epic_and_unassigns(jira) -> None:
    server.update_issue("MIC-1", epic="", assignee="")
    assert jira.bodies("PUT", "/issue/MIC-1") == [{"fields": {server.EPIC_LINK_FIELD: None}}]
    assert jira.bodies("PUT", "/issue/MIC-1/assignee") == [{"name": None}]


def test_update_without_fields_sends_nothing(jira) -> None:
    server.update_issue("MIC-1")
    assert jira.calls == []


TRANSITIONS = {
    "transitions": [
        {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
        {"id": "21", "name": "Done", "to": {"name": "Closed"}},
    ]
}


@pytest.mark.parametrize("wanted, transition_id", [("in progress", "11"), ("Done", "21")])
def test_transition_matches_status_then_name(jira, wanted, transition_id) -> None:
    jira.responses[("GET", "/issue/MIC-1/transitions")] = TRANSITIONS
    server.transition_issue("MIC-1", wanted)
    (body,) = jira.bodies("POST", "/issue/MIC-1/transitions")
    assert body == {"transition": {"id": transition_id}}


def test_transition_unknown_lists_options(jira) -> None:
    jira.responses[("GET", "/issue/MIC-1/transitions")] = TRANSITIONS
    with pytest.raises(ToolError, match="Start Progress -> In Progress"):
        server.transition_issue("MIC-1", "Won't Do")


def test_sprint_names_from_string_and_object() -> None:
    raw = [
        "com.atlassian.greenhopper.service.sprint.Sprint@1[id=5,rapidViewId=301,"
        "state=ACTIVE,name=Vivarium Sprint 199,startDate=x]",
        {"id": 6, "name": "Next"},
    ]
    assert server._sprint_names(raw) == ["Vivarium Sprint 199", "Next"]


def test_get_issue_shapes_fields(jira) -> None:
    jira.responses[("GET", "/issue/MIC-1")] = {
        "key": "MIC-1",
        "fields": {
            "summary": "s",
            "priority": {"name": "Major"},
            "fixVersions": [{"name": "AI"}],
            server.STORY_POINTS_FIELD: 1.01,
            "assignee": {"displayName": "Ann Lee", "name": "alee"},
            "issuelinks": [
                {
                    "type": LINK_TYPES[0],
                    "outwardIssue": {
                        "key": "MIC-2",
                        "fields": {"summary": "t", "status": {"name": "To Do"}},
                    },
                }
            ],
            "comment": {"comments": [{"body": "a"}, {"body": "b"}, {"body": "c"}]},
        },
    }
    issue = server.get_issue("MIC-1", include_comments=2)
    assert issue["story_points"] == 1.01
    assert issue["priority"] == "Major"
    assert issue["fix_versions"] == ["AI"]
    assert issue["assignee"] == "Ann Lee (alee)"
    assert issue["links"] == [
        {"relationship": "blocks", "issue_key": "MIC-2", "summary": "t", "status": "To Do"}
    ]
    assert [c["body"] for c in issue["comments"]] == ["b", "c"]
