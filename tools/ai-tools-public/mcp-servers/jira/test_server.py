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


def test_token_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", " abc123 ")
    assert server._token() == "abc123"


@pytest.mark.parametrize("value", [None, "", "${user_config.jira_api_token}"])
def test_token_missing_raises(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("JIRA_API_TOKEN", value)
    with pytest.raises(ToolError, match="Configure options"):
        server._token()


@pytest.mark.parametrize(
    "value, expected", [("PROJ", "PROJ"), ("", "ABC"), ("${user_config.jira_project}", "ABC")]
)
def test_setting_falls_back_to_default(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("JIRA_PROJECT", value)
    assert server._setting("JIRA_PROJECT", "ABC") == expected


@pytest.mark.parametrize(
    "phrase, inward, outward",
    [
        ("blocks", "ABC-1", "ABC-2"),
        ("Blocks", "ABC-1", "ABC-2"),
        ("is blocked by", "ABC-2", "ABC-1"),
        ("related to", "ABC-1", "ABC-2"),
    ],
)
def test_link_direction(jira, phrase, inward, outward) -> None:
    server.link_issues("ABC-1", "ABC-2", phrase)
    (body,) = jira.bodies("POST", "/issueLink")
    assert body["inwardIssue"]["key"] == inward
    assert body["outwardIssue"]["key"] == outward


def test_unknown_link_lists_options(jira) -> None:
    with pytest.raises(ToolError, match="is blocked by"):
        server.link_issues("ABC-1", "ABC-2", "duplicates")


def test_create_issue_sets_new_fields(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "ABC-9"}
    result = server.create_issue(
        summary="s",
        description="d",
        project="ABC",
        issue_type="Story",
        priority="Major",
        labels=["AI_Tools"],
        fix_versions=["AI"],
        story_points=2.01,
        epic="ABC-100",
        links={"is blocked by": ["ABC-5"]},
    )
    (body,) = jira.bodies("POST", "/issue")
    fields = body["fields"]
    assert fields["project"] == {"key": "ABC"}
    assert fields["priority"] == {"name": "Major"}
    assert fields["fixVersions"] == [{"name": "AI"}]
    assert fields[server.STORY_POINTS_FIELD] == 2.01
    assert fields[server.EPIC_LINK_FIELD] == "ABC-100"
    assert server.EPIC_NAME_FIELD not in fields
    assert result == {
        "key": "ABC-9",
        "url": "https://jira.ihme.washington.edu/browse/ABC-9",
        "sprint_failure": None,
        "link_failures": [],
    }
    (link,) = jira.bodies("POST", "/issueLink")
    assert (link["inwardIssue"]["key"], link["outwardIssue"]["key"]) == ("ABC-5", "ABC-9")


def test_create_epic_defaults_epic_name(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "ABC-9"}
    server.create_issue(summary="Big work", description="d", project="ABC", issue_type="Epic")
    (body,) = jira.bodies("POST", "/issue")
    assert body["fields"][server.EPIC_NAME_FIELD] == "Big work"


def test_create_issue_needs_a_project(jira, monkeypatch) -> None:
    monkeypatch.setattr(server, "DEFAULT_PROJECT", "")
    with pytest.raises(ToolError, match="Pass project"):
        server.create_issue(summary="s", description="d")
    assert not jira.bodies("POST", "/issue")


def test_create_issue_uses_default_project(jira, monkeypatch) -> None:
    monkeypatch.setattr(server, "DEFAULT_PROJECT", "XYZ")
    jira.responses[("POST", "/issue")] = {"key": "XYZ-1"}
    server.create_issue(summary="s", description="d")
    (body,) = jira.bodies("POST", "/issue")
    assert body["fields"]["project"] == {"key": "XYZ"}


def set_boards(jira: FakeJira, sprints_by_board: dict[int, list[tuple[int, str]]]) -> None:
    """Make every listed scrum board answer with its sprints."""
    jira.responses[("GET", "/board")] = {
        "values": [{"id": board} for board in sprints_by_board],
        "isLast": True,
    }
    for board, sprints in sprints_by_board.items():
        jira.responses[("GET", f"/board/{board}/sprint")] = {
            "values": [{"id": i, "name": name} for i, name in sprints],
            "isLast": True,
        }


def test_active_sprint_is_the_one_shared_by_all_boards(jira) -> None:
    set_boards(
        jira,
        {
            1: [(199, "Sprint 199")],
            2: [(7, "Other team"), (199, "Sprint 199")],
            3: [],
        },
    )
    assert server._resolve_sprint("ABC", "active") == 199


def test_active_sprint_ambiguous_raises(jira) -> None:
    set_boards(jira, {1: [(1, "One")], 2: [(2, "Two")]})
    with pytest.raises(ToolError, match=r"Cannot tell.*\['One', 'Two'\]"):
        server._resolve_sprint("ABC", "active")


def test_sprint_by_name_across_boards(jira) -> None:
    set_boards(jira, {1: [(1, "Sprint 199")], 2: [(1, "Sprint 199"), (2, "Next")]})
    assert server._resolve_sprint("ABC", "Next") == 2
    assert server._resolve_sprint("ABC", "Sprint 199") == 1
    with pytest.raises(ToolError, match="No active or future sprint"):
        server._resolve_sprint("ABC", "Missing")


def test_sprint_name_on_two_sprints_raises(jira) -> None:
    set_boards(jira, {1: [(1, "Dup")], 2: [(2, "Dup")]})
    with pytest.raises(ToolError, match=r"IDs \[1, 2\]"):
        server._resolve_sprint("ABC", "Dup")


def test_project_without_scrum_board_raises(jira) -> None:
    jira.responses[("GET", "/board")] = {"values": [], "isLast": True}
    with pytest.raises(ToolError, match="No scrum board lists project ABC"):
        server._resolve_sprint("ABC", "active")


def test_sprint_uses_the_issue_project(jira) -> None:
    set_boards(jira, {1: [(5, "Sprint 5")]})
    result = server.update_issue("XYZ-3", sprint="Sprint 5")
    assert result["sprint_failure"] is None
    board_call = next(kw for m, p, kw in jira.calls if p == "/board")
    assert board_call["params"]["projectKeyOrId"] == "XYZ"
    assert jira.bodies("POST", "/sprint/5/issue") == [{"issues": ["XYZ-3"]}]


def test_create_issue_reports_sprint_failure(jira) -> None:
    jira.responses[("POST", "/issue")] = {"key": "ABC-9"}
    jira.responses[("GET", "/board")] = {"values": [], "isLast": True}
    result = server.create_issue(summary="s", description="d", project="ABC", sprint="active")
    assert result["key"] == "ABC-9"
    assert "No scrum board" in result["sprint_failure"]


def test_update_moves_to_backlog(jira) -> None:
    result = server.update_issue("ABC-1", sprint="Backlog")
    assert result["sprint_failure"] is None
    assert jira.calls == [
        ("POST", "/backlog/issue", {"json": {"issues": ["ABC-1"]}, "base": server.AGILE_URL})
    ]


def test_update_refuses_dropped_macro(jira) -> None:
    jira.responses[("GET", "/issue/ABC-1")] = {
        "fields": {"description": "text {code:python}x = 1{code} {panel}p{panel}"}
    }
    with pytest.raises(ToolError, match=r"\['code'\]"):
        server.update_issue("ABC-1", description="{panel}p{panel} only")
    assert not jira.bodies("PUT", "/issue/ABC-1")


def test_update_force_overwrites(jira) -> None:
    server.update_issue("ABC-1", description="plain", force=True)
    assert jira.bodies("PUT", "/issue/ABC-1") == [{"fields": {"description": "plain"}}]


def test_update_clears_epic_and_unassigns(jira) -> None:
    server.update_issue("ABC-1", epic="", assignee="")
    assert jira.bodies("PUT", "/issue/ABC-1") == [{"fields": {server.EPIC_LINK_FIELD: None}}]
    assert jira.bodies("PUT", "/issue/ABC-1/assignee") == [{"name": None}]


def test_update_without_fields_sends_nothing(jira) -> None:
    server.update_issue("ABC-1")
    assert jira.calls == []


TRANSITIONS = {
    "transitions": [
        {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
        {"id": "21", "name": "Done", "to": {"name": "Closed"}},
    ]
}


@pytest.mark.parametrize("wanted, transition_id", [("in progress", "11"), ("Done", "21")])
def test_transition_matches_status_then_name(jira, wanted, transition_id) -> None:
    jira.responses[("GET", "/issue/ABC-1/transitions")] = TRANSITIONS
    server.transition_issue("ABC-1", wanted)
    (body,) = jira.bodies("POST", "/issue/ABC-1/transitions")
    assert body == {"transition": {"id": transition_id}}


def test_transition_unknown_lists_options(jira) -> None:
    jira.responses[("GET", "/issue/ABC-1/transitions")] = TRANSITIONS
    with pytest.raises(ToolError, match="Start Progress -> In Progress"):
        server.transition_issue("ABC-1", "Won't Do")


def test_sprint_names_from_string_and_object() -> None:
    raw = [
        "com.atlassian.greenhopper.service.sprint.Sprint@1[id=5,rapidViewId=1,"
        "state=ACTIVE,name=Sprint 199,startDate=x]",
        {"id": 6, "name": "Next"},
    ]
    assert server._sprint_names(raw) == ["Sprint 199", "Next"]


def test_get_issue_shapes_fields(jira) -> None:
    jira.responses[("GET", "/issue/ABC-1")] = {
        "key": "ABC-1",
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
                        "key": "ABC-2",
                        "fields": {"summary": "t", "status": {"name": "To Do"}},
                    },
                }
            ],
            "comment": {"comments": [{"body": "a"}, {"body": "b"}, {"body": "c"}]},
        },
    }
    issue = server.get_issue("ABC-1", include_comments=2)
    assert issue["story_points"] == 1.01
    assert issue["priority"] == "Major"
    assert issue["fix_versions"] == ["AI"]
    assert issue["assignee"] == "Ann Lee (alee)"
    assert issue["links"] == [
        {"relationship": "blocks", "issue_key": "ABC-2", "summary": "t", "status": "To Do"}
    ]
    assert [c["body"] for c in issue["comments"]] == ["b", "c"]
