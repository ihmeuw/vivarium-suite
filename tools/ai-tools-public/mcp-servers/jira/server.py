# Inline script metadata (PEP 723): `uv run --script` reads it to install the dependencies.
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.27",
#     "mcp>=2.3,<3",
# ]
# ///
"""Jira MCP server for IHME's Jira.

Gets, searches, creates, updates, comments on, links, and transitions issues,
including story points, priority, fix versions, sprint, and epic.

Its settings come from the plugin's ``userConfig`` options, which Claude Code
passes in as environment variables: ``JIRA_API_TOKEN`` (a Jira personal access
token) and ``JIRA_PROJECT`` (an optional default project).
"""

import os
import re
from collections.abc import Callable
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def _setting(name: str, default: str = "") -> str:
    """Return an environment setting, or the default if it is empty or unsubstituted."""
    value = os.environ.get(name, "").strip()
    # An option the user left empty can arrive as the literal ${user_config.*} text.
    if not value or value.startswith("${"):
        return default
    return value


BASE_URL = "https://jira.ihme.washington.edu"
API_URL = f"{BASE_URL}/rest/api/2"
AGILE_URL = f"{BASE_URL}/rest/agile/1.0"
DEFAULT_PROJECT = _setting("JIRA_PROJECT")
CONFIGURE_HINT = "/plugin > Installed > simsci > Configure options"

# IHME's custom field IDs differ from Atlassian's documented defaults. Found
# with GET /rest/api/2/field.
SPRINT_FIELD = "customfield_10000"
EPIC_LINK_FIELD = "customfield_10001"
EPIC_NAME_FIELD = "customfield_10004"
STORY_POINTS_FIELD = "customfield_10008"

ISSUE_FIELDS = ",".join(
    [
        "summary",
        "status",
        "issuetype",
        "priority",
        "labels",
        "fixVersions",
        "components",
        "assignee",
        "reporter",
        "parent",
        "subtasks",
        "issuelinks",
        "description",
        "comment",
        "created",
        "updated",
        SPRINT_FIELD,
        EPIC_LINK_FIELD,
        STORY_POINTS_FIELD,
    ]
)
SEARCH_FIELDS = ",".join(
    [
        "summary",
        "status",
        "issuetype",
        "priority",
        "labels",
        "fixVersions",
        "assignee",
        "parent",
        "updated",
        STORY_POINTS_FIELD,
    ]
)
# Block macros that a rewritten description can silently drop.
MACRO_RE = re.compile(
    r"\{(code|noformat|panel|quote|info|warning|note|tip|expand|color)(?::[^}]*)?\}"
)
mcp = MCPServer("jira")


# ─── HTTP ───────────────────────────────────────────────────────────────────


def _token() -> str:
    """Return the Jira personal access token."""
    token = _setting("JIRA_API_TOKEN")
    if not token:
        raise ToolError(
            "No Jira token is set. Create a personal access token in Jira (Profile > "
            f"Personal Access Tokens), then enter it in {CONFIGURE_HINT}, and restart "
            "Claude Code."
        )
    return token


def _error_detail(response: httpx.Response) -> str:
    """Return Jira's error messages from a failed response, or its raw text."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if not isinstance(body, dict):
        return str(body)[:500]
    messages = list(body.get("errorMessages") or [])
    messages += [f"{field}: {msg}" for field, msg in (body.get("errors") or {}).items()]
    return "; ".join(messages) or str(body)[:500]


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    base: str = API_URL,
) -> Any:
    """Send a request to Jira and return the decoded JSON body."""
    try:
        response = httpx.request(
            method,
            f"{base}{path}",
            headers={
                "Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json",
            },
            params=params,
            json=json,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach Jira for {method} {path}: {exc!r}") from exc
    if response.status_code == 401:
        raise ToolError("Jira returned 401. Check that JIRA_API_TOKEN is valid.")
    if response.is_error:
        raise ToolError(
            f"Jira returned {response.status_code} for {method} {path}: "
            f"{_error_detail(response)}"
        )
    if not response.content:
        return {}
    return response.json()


# ─── helpers ────────────────────────────────────────────────────────────────


def _url(key: str) -> str:
    return f"{BASE_URL}/browse/{key}"


def _name(obj: dict[str, Any] | None, default: str = "") -> str:
    return str((obj or {}).get("name", default))


def _person(user: dict[str, Any] | None) -> str:
    """Format a Jira user as 'Display Name (username)'."""
    if not user:
        return "Unassigned"
    return f"{user.get('displayName', '')} ({user.get('name', '')})"


def _sprint_names(raw: list[Any] | None) -> list[str]:
    """Return sprint names from the Sprint field, which Jira DC may encode as strings."""
    names = []
    for sprint in raw or []:
        if isinstance(sprint, dict):
            names.append(sprint.get("name", ""))
        else:
            match = re.search(r"name=([^,\]]*)", str(sprint))
            if match:
                names.append(match.group(1))
    return names


def _resolve_username(username: str) -> str:
    """Resolve 'me' to the token owner's username; pass anything else through."""
    if username == "me":
        return str(_request("GET", "/myself").get("name", ""))
    return username


def _paged_values(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every value from a paged Jira agile API list."""
    values: list[dict[str, Any]] = []
    while True:
        page = _request(
            "GET", path, params={**params, "startAt": len(values)}, base=AGILE_URL
        )
        batch = page.get("values", [])
        values += batch
        if page.get("isLast", True) or not batch:
            return values


def _project_sprints(project: str, state: str) -> list[list[dict[str, Any]]]:
    """Return the sprints in a state on each scrum board that lists the project."""
    boards = _paged_values("/board", {"projectKeyOrId": project, "type": "scrum"})
    if not boards:
        raise ToolError(f"No scrum board lists project {project}, so it has no sprints.")
    return [_paged_values(f"/board/{b['id']}/sprint", {"state": state}) for b in boards]


def _resolve_sprint(project: str, sprint: int | str) -> int:
    """Resolve a sprint ID, 'active', or an exact sprint name to a sprint ID.

    A project can be on several scrum boards, so names and 'active' are resolved
    across all of them. 'active' is the sprint that is active on every board that
    has an active sprint.
    """
    if isinstance(sprint, int):
        return sprint
    if sprint.lower() in ("active", "current"):
        per_board = [
            {int(s["id"]): s.get("name", "") for s in sprints}
            for sprints in _project_sprints(project, "active")
            if sprints
        ]
        common = set.intersection(*(set(b) for b in per_board)) if per_board else set()
        if len(common) == 1:
            return common.pop()
        names = sorted({name for b in per_board for name in b.values()})
        raise ToolError(
            f"Cannot tell which sprint is active in {project}. Active sprints: "
            f"{names or 'none'}. Pass the sprint name or ID."
        )
    ids = {
        int(s["id"])
        for sprints in _project_sprints(project, "active,future")
        for s in sprints
        if s.get("name") == sprint
    }
    if len(ids) == 1:
        return ids.pop()
    if not ids:
        raise ToolError(f"No active or future sprint in {project} is named '{sprint}'.")
    raise ToolError(
        f"Several sprints in {project} are named '{sprint}': IDs {sorted(ids)}. "
        "Pass the sprint ID."
    )


def _add_to_sprint(key: str, sprint: int | str) -> None:
    """Move an issue into a sprint, or out of all sprints if sprint is 'backlog'."""
    if isinstance(sprint, str) and sprint.lower() == "backlog":
        _request("POST", "/backlog/issue", json={"issues": [key]}, base=AGILE_URL)
        return
    sprint_id = _resolve_sprint(key.split("-")[0], sprint)
    _request("POST", f"/sprint/{sprint_id}/issue", json={"issues": [key]}, base=AGILE_URL)


def _link_types() -> list[dict[str, Any]]:
    link_types: list[dict[str, Any]] = _request("GET", "/issueLinkType").get(
        "issueLinkTypes", []
    )
    return link_types


def _resolve_link(phrase: str) -> tuple[str, bool]:
    """Return the link type name for a phrase, and whether the subject is the outward side.

    The phrase can be the type name ('Blocks'), its outward phrase ('blocks'), or
    its inward phrase ('is blocked by'), in any case.
    """
    wanted = phrase.strip().lower()
    types = _link_types()
    for link_type in types:
        if wanted in (link_type["name"].lower(), link_type["outward"].lower()):
            return link_type["name"], True
        if wanted == link_type["inward"].lower():
            return link_type["name"], False
    options = sorted(
        {p for t in types for p in (t["name"], t["outward"], t["inward"])}, key=str.lower
    )
    raise ToolError(f"Unknown link type '{phrase}'. Options: {options}")


def _link(subject: str, phrase: str, target: str) -> None:
    """Create the link '<subject> <phrase> <target>'."""
    type_name, subject_is_outward = _resolve_link(phrase)
    # In POST /issueLink, inwardIssue is the issue that the outward phrase
    # describes: for Blocks, inwardIssue blocks outwardIssue.
    inward, outward = (subject, target) if subject_is_outward else (target, subject)
    _request(
        "POST",
        "/issueLink",
        json={
            "type": {"name": type_name},
            "inwardIssue": {"key": inward},
            "outwardIssue": {"key": outward},
        },
    )


def _apply_links(key: str, links: dict[str, list[str]] | None) -> list[dict[str, str]]:
    """Create each requested link and return the ones that failed."""
    failures = []
    for phrase, targets in (links or {}).items():
        for target in targets:
            try:
                _link(key, phrase, target)
            except ToolError as exc:
                failures.append({"link": f"{key} {phrase} {target}", "error": str(exc)})
    return failures


def _issue_links(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    links = []
    for link in raw:
        link_type = link.get("type") or {}
        if "outwardIssue" in link:
            relationship, other = link_type.get("outward", ""), link["outwardIssue"]
        else:
            relationship, other = link_type.get("inward", ""), link.get("inwardIssue") or {}
        fields = other.get("fields") or {}
        links.append(
            {
                "relationship": relationship,
                "issue_key": other.get("key", ""),
                "summary": fields.get("summary", ""),
                "status": _name(fields.get("status")),
            }
        )
    return links


def _github_links(key: str) -> list[dict[str, Any]]:
    """Return the GitHub branch, commit, and pull request links on an issue.

    The GitHub-Jira integration maintains these, so they are the record of the
    code change. A failure returns an empty list, not an error.
    """
    try:
        data = _request("GET", f"/issue/{key}/remotelink")
    except ToolError:
        return []
    links = []
    for remote in data if isinstance(data, list) else []:
        if (remote.get("application") or {}).get("type") != "com.github":
            continue
        obj = remote.get("object") or {}
        status = obj.get("status") or {}
        links.append(
            {
                "relationship": remote.get("relationship", ""),
                "url": obj.get("url", ""),
                "title": obj.get("title", ""),
                # GitHub's own state, e.g. "Merged", "Open", or "Closed". Do not
                # use "resolved" for this: a closed, unmerged PR is resolved too.
                "state": (status.get("icon") or {}).get("title", ""),
            }
        )
    return links


def _check_macros(key: str, new_description: str) -> None:
    """Refuse a description that drops block macros present in the current one."""
    current = _request("GET", f"/issue/{key}", params={"fields": "description"})
    old = (current.get("fields") or {}).get("description") or ""
    dropped = set(MACRO_RE.findall(old)) - set(MACRO_RE.findall(new_description))
    if dropped:
        raise ToolError(
            f"The new description of {key} drops these macros from the current one: "
            f"{sorted(dropped)}. Read the current description with get_issue and keep "
            "them, or pass force=True to overwrite."
        )


def _set_fields(
    fields: dict[str, Any],
    *,
    priority: str | None,
    labels: list[str] | None,
    fix_versions: list[str] | None,
    story_points: float | None,
    epic: str | None,
) -> None:
    """Add the fields shared by create_issue and update_issue to a fields payload."""
    if priority is not None:
        fields["priority"] = {"name": priority}
    if labels is not None:
        fields["labels"] = labels
    if fix_versions is not None:
        fields["fixVersions"] = [{"name": v} for v in fix_versions]
    if story_points is not None:
        fields[STORY_POINTS_FIELD] = story_points
    if epic is not None:
        fields[EPIC_LINK_FIELD] = epic or None


# ─── tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def get_issue(key: str, include_comments: int = 5) -> dict[str, Any]:
    """Get a Jira issue, including story points, fix versions, sprints, links, and comments.

    The description is returned as raw Jira wiki markup.

    Parameters
    ----------
    key
        Issue key, e.g. 'ABC-123'.
    include_comments
        Number of most recent comments to return. Pass 0 for none.
    """
    data = _request("GET", f"/issue/{key}", params={"fields": ISSUE_FIELDS})
    fields = data.get("fields", {})
    comments = (fields.get("comment") or {}).get("comments", [])
    recent = comments[-include_comments:] if include_comments > 0 else []
    parent = fields.get("parent") or {}
    return {
        "key": data.get("key", key),
        "summary": fields.get("summary", ""),
        "status": _name(fields.get("status")),
        "issue_type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
        "story_points": fields.get(STORY_POINTS_FIELD),
        "fix_versions": [_name(v) for v in fields.get("fixVersions") or []],
        "labels": fields.get("labels", []),
        "components": [_name(c) for c in fields.get("components") or []],
        "sprints": _sprint_names(fields.get(SPRINT_FIELD)),
        "epic": fields.get(EPIC_LINK_FIELD),
        "parent": parent.get("key"),
        "subtasks": [
            {
                "key": s.get("key", ""),
                "summary": (s.get("fields") or {}).get("summary", ""),
                "status": _name((s.get("fields") or {}).get("status")),
            }
            for s in fields.get("subtasks") or []
        ],
        "assignee": _person(fields.get("assignee")),
        "reporter": _person(fields.get("reporter")),
        "created": (fields.get("created") or "")[:10],
        "updated": (fields.get("updated") or "")[:10],
        "description": fields.get("description") or "",
        "links": _issue_links(fields.get("issuelinks") or []),
        "github_links": _github_links(data.get("key", key)),
        "comments": [
            {
                "author": (c.get("author") or {}).get("displayName", ""),
                "created": (c.get("created") or "")[:10],
                "body": c.get("body", ""),
            }
            for c in recent
        ],
        "url": _url(data.get("key", key)),
    }


@mcp.tool()
def search(jql: str, limit: int = 20) -> dict[str, Any]:
    """Search Jira issues with JQL.

    Each result includes story points, priority, fix versions, labels, and the
    parent of a sub-task. Story points can be filtered in JQL as '"Story Points"',
    e.g. '"Story Points" is EMPTY'. To list an issue's sub-tasks, use
    'parent = ABC-123'.

    Parameters
    ----------
    jql
        JQL query, e.g. 'project = ABC AND status = "In Progress" ORDER BY updated DESC'.
    limit
        Maximum number of issues to return, at most 200.
    """
    limit = max(1, min(limit, 200))
    issues: list[dict[str, Any]] = []
    total = 0
    while len(issues) < limit:
        page = _request(
            "GET",
            "/search",
            params={
                "jql": jql,
                "startAt": len(issues),
                "maxResults": min(limit - len(issues), 50),
                "fields": SEARCH_FIELDS,
            },
        )
        total = page.get("total", 0)
        batch = page.get("issues", [])
        for issue in batch:
            fields = issue.get("fields", {})
            issues.append(
                {
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", ""),
                    "status": _name(fields.get("status")),
                    "issue_type": _name(fields.get("issuetype")),
                    "priority": _name(fields.get("priority")),
                    "story_points": fields.get(STORY_POINTS_FIELD),
                    "fix_versions": [_name(v) for v in fields.get("fixVersions") or []],
                    "labels": fields.get("labels", []),
                    "assignee": _person(fields.get("assignee")),
                    "parent": (fields.get("parent") or {}).get("key"),
                    "updated": (fields.get("updated") or "")[:10],
                    "url": _url(issue.get("key", "")),
                }
            )
        if not batch or len(issues) >= total:
            break
    return {"total": total, "issues": issues}


@mcp.tool()
def create_issue(
    summary: str,
    description: str,
    issue_type: str = "Task",
    project: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    fix_versions: list[str] | None = None,
    story_points: float | None = None,
    epic: str | None = None,
    epic_name: str | None = None,
    parent: str | None = None,
    assignee: str | None = None,
    sprint: int | str | None = None,
    links: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Create a Jira issue. This writes to Jira.

    The issue is created first. Sprint and link failures after that do not undo
    it: they are reported in 'sprint_failure' and 'link_failures'.

    Parameters
    ----------
    summary
        Issue title.
    description
        Issue body in Jira wiki markup, not Markdown.
    issue_type
        'Task', 'Story', 'Bug', or 'Epic', or a sub-task type: 'Sub-Task',
        'Technical Task', 'Request Sub-Task', or 'Data Request Sub-Task'.
    project
        Project key, e.g. 'ABC'. Required unless the user set a default project in
        the plugin's options.
    priority
        'Critical', 'Major', 'Medium', 'Minor', 'Trivial', or 'Blocked'.
    labels
        Labels to set.
    fix_versions
        Names of existing fix versions in the project.
    story_points
        Story points. Some projects leave this field off some issue types, such as
        Bug, and Jira then rejects the value.
    epic
        Key of the epic to put the issue in. Not valid for an Epic or a sub-task.
    epic_name
        Short epic label. Used only when issue_type is 'Epic'; defaults to the summary.
    parent
        Parent issue key. Required for a sub-task type, and must be in the same
        project. A sub-task cannot be the parent of another sub-task.
    assignee
        Jira username, or 'me' for the token owner.
    sprint
        Sprint ID, exact sprint name, or 'active'. Omit to leave the issue in the backlog.
    links
        Links from the new issue, as {phrase: [issue keys]}. A phrase is a link
        type's outward or inward phrase: 'blocks', 'is blocked by', 'related to',
        'causes', 'is caused by', 'split to', 'split from'.
    """
    project = project or DEFAULT_PROJECT
    if not project:
        raise ToolError(f"Pass project, or set a default project in {CONFIGURE_HINT}.")
    fields: dict[str, Any] = {
        "project": {"key": project},
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type},
    }
    if issue_type.lower() == "epic":
        fields[EPIC_NAME_FIELD] = epic_name or summary
    if parent is not None:
        fields["parent"] = {"key": parent}
    if assignee is not None:
        fields["assignee"] = {"name": _resolve_username(assignee)}
    _set_fields(
        fields,
        priority=priority,
        labels=labels,
        fix_versions=fix_versions,
        story_points=story_points,
        epic=epic,
    )
    try:
        key = _request("POST", "/issue", json={"fields": fields})["key"]
    except ToolError as exc:
        # Jira answers an unknown project key with "project is required".
        if "project: project is required" in str(exc):
            raise ToolError(
                f"{exc} Jira gives this error when it does not know project '{project}'. "
                "Check the project key."
            ) from exc
        raise

    sprint_failure = None
    if sprint is not None:
        try:
            _add_to_sprint(key, sprint)
        except ToolError as exc:
            sprint_failure = str(exc)
    return {
        "key": key,
        "url": _url(key),
        "sprint_failure": sprint_failure,
        "link_failures": _apply_links(key, links),
    }


@mcp.tool()
def update_issue(
    key: str,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    fix_versions: list[str] | None = None,
    story_points: float | None = None,
    epic: str | None = None,
    assignee: str | None = None,
    sprint: int | str | None = None,
    links: dict[str, list[str]] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Update a Jira issue. This writes to Jira. Only the arguments you pass change.

    Parameters
    ----------
    key
        Issue key, e.g. 'ABC-123'.
    summary
        New title.
    description
        New body in Jira wiki markup. Replaces the whole description. The update is
        refused if it drops block macros such as {code} or {panel} from the current
        description, unless force is True.
    priority
        'Critical', 'Major', 'Medium', 'Minor', 'Trivial', or 'Blocked'.
    labels
        Full replacement list of labels. To add one, read the current labels with
        get_issue and pass the combined list.
    fix_versions
        Full replacement list of fix version names.
    story_points
        New story points.
    epic
        Key of the epic to move the issue to. Pass '' to remove it from its epic.
    assignee
        Jira username, 'me' for the token owner, or '' to unassign.
    sprint
        Sprint ID, exact sprint name, or 'active' to move the issue into, or
        'backlog' to remove it from its open sprint.
    links
        Links to add, as {phrase: [issue keys]}. Existing links are kept. See
        create_issue for the phrases.
    force
        Overwrite the description even if it drops block macros.
    """
    fields: dict[str, Any] = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        if not force:
            _check_macros(key, description)
        fields["description"] = description
    _set_fields(
        fields,
        priority=priority,
        labels=labels,
        fix_versions=fix_versions,
        story_points=story_points,
        epic=epic,
    )
    if fields:
        _request("PUT", f"/issue/{key}", json={"fields": fields})
    if assignee is not None:
        _request(
            "PUT",
            f"/issue/{key}/assignee",
            json={"name": _resolve_username(assignee) if assignee else None},
        )

    sprint_failure = None
    if sprint is not None:
        try:
            _add_to_sprint(key, sprint)
        except ToolError as exc:
            sprint_failure = str(exc)
    return {
        "key": key,
        "url": _url(key),
        "sprint_failure": sprint_failure,
        "link_failures": _apply_links(key, links),
    }


@mcp.tool()
def convert_to_subtask(
    key: str,
    parent: str,
    issue_type: str = "Sub-Task",
    close_status: str = "Duplicate",
) -> dict[str, Any]:
    """Replace an issue with a copy that is a sub-task of another issue. This writes to Jira.

    Jira's API cannot change an issue into a sub-task, so this creates a new
    sub-task under the parent and closes the original. The sub-task gets a new
    key. The original keeps its comments, history, and GitHub links. The two are
    linked, and each gets a comment that points to the other. Tell the user about
    the new key and confirm before calling.

    The summary, description, priority, labels, fix versions, and assignee are
    copied. Story points, epic, and sprints are not, because sub-tasks cannot hold
    them in every project. Their old values are returned in 'not_copied'. Steps
    after the create that fail do not undo it: they are reported in 'failures'.

    Parameters
    ----------
    key
        Issue to convert, e.g. 'ABC-123'. It must not be a sub-task or have
        sub-tasks.
    parent
        Issue the new sub-task belongs to, e.g. 'ABC-100'. It must be in the same
        project and must not be a sub-task.
    issue_type
        Sub-task type: 'Sub-Task', 'Technical Task', 'Request Sub-Task', or
        'Data Request Sub-Task'.
    close_status
        Status or transition name for the original, e.g. 'Duplicate' or "Won't Do".
    """
    if key == parent:
        raise ToolError("An issue cannot be its own parent.")
    original = _request(
        "GET",
        f"/issue/{key}",
        params={
            "fields": "summary,description,priority,labels,fixVersions,assignee,issuetype,"
            f"project,subtasks,{STORY_POINTS_FIELD},{EPIC_LINK_FIELD},{SPRINT_FIELD}"
        },
    )["fields"]
    target = _request("GET", f"/issue/{parent}", params={"fields": "issuetype,project"})[
        "fields"
    ]
    if (original.get("issuetype") or {}).get("subtask"):
        raise ToolError(f"{key} is already a sub-task.")
    if original.get("subtasks"):
        raise ToolError(f"{key} has sub-tasks of its own, so it cannot become a sub-task.")
    if (target.get("issuetype") or {}).get("subtask"):
        raise ToolError(f"{parent} is a sub-task, so it cannot have sub-tasks.")
    project = (original.get("project") or {}).get("key")
    if project != (target.get("project") or {}).get("key"):
        raise ToolError(
            f"{key} and {parent} are in different projects. Move {key} into the "
            "parent's project in Jira first."
        )

    created = create_issue(
        summary=original.get("summary", ""),
        description=original.get("description") or "",
        issue_type=issue_type,
        project=project,
        priority=_name(original.get("priority")) or None,
        labels=original.get("labels") or None,
        fix_versions=[_name(v) for v in original.get("fixVersions") or []] or None,
        parent=parent,
        assignee=(original.get("assignee") or {}).get("name"),
    )
    new_key = created["key"]

    failures = []
    steps: list[tuple[str, Callable[[], object]]] = [
        ("link", lambda: _link(key, "related to", new_key)),
        (
            "comment on the sub-task",
            lambda: add_comment(
                new_key,
                f"Converted from {key}. Its comments, history, and GitHub links stay there.",
            ),
        ),
        (
            "comment on the original",
            lambda: add_comment(key, f"Replaced by sub-task {new_key} of {parent}."),
        ),
        ("close the original", lambda: transition_issue(key, close_status)),
    ]
    for step, action in steps:
        try:
            action()
        except ToolError as exc:
            failures.append({"step": step, "error": str(exc)})

    not_copied = {
        "story_points": original.get(STORY_POINTS_FIELD),
        "epic": original.get(EPIC_LINK_FIELD),
        "sprints": _sprint_names(original.get(SPRINT_FIELD)),
    }
    return {
        "key": new_key,
        "url": _url(new_key),
        "original": key,
        "parent": parent,
        "failures": failures,
        "not_copied": {name: value for name, value in not_copied.items() if value},
    }


@mcp.tool()
def add_comment(issue_key: str, body: str) -> dict[str, Any]:
    """Add a comment to a Jira issue as the token owner. This writes to Jira.

    Parameters
    ----------
    issue_key
        Issue key, e.g. 'ABC-123'.
    body
        Comment in Jira wiki markup, not Markdown. Use [~username] to notify someone.
    """
    data = _request("POST", f"/issue/{issue_key}/comment", json={"body": body})
    return {
        "id": data.get("id"),
        "author": (data.get("author") or {}).get("displayName", ""),
        "url": f"{_url(issue_key)}?focusedCommentId={data.get('id')}",
    }


@mcp.tool()
def link_issues(from_issue: str, to_issue: str, link_type: str) -> dict[str, str]:
    """Create the link '<from_issue> <link_type> <to_issue>'. This writes to Jira.

    Jira allows duplicate links, so check the current links with get_issue first.

    Parameters
    ----------
    from_issue
        Key of the subject issue.
    to_issue
        Key of the object issue.
    link_type
        A link type name or phrase: 'blocks', 'is blocked by', 'related to',
        'causes', 'is caused by', 'split to', 'split from'.
    """
    _link(from_issue, link_type, to_issue)
    return {"link": f"{from_issue} {link_type} {to_issue}"}


@mcp.tool()
def list_transitions(issue_key: str) -> list[dict[str, Any]]:
    """List the workflow transitions available from an issue's current status.

    Parameters
    ----------
    issue_key
        Issue key, e.g. 'ABC-123'.
    """
    data = _request(
        "GET", f"/issue/{issue_key}/transitions", params={"expand": "transitions.fields"}
    )
    return [
        {
            "transition": t["name"],
            "to_status": _name(t.get("to")),
            "required_fields": sorted(
                k for k, f in (t.get("fields") or {}).items() if f.get("required")
            ),
        }
        for t in data.get("transitions", [])
    ]


@mcp.tool()
def transition_issue(
    issue_key: str, to_status: str, fields: dict[str, Any] | None = None
) -> dict[str, str]:
    """Move a Jira issue to a new status through its workflow. This writes to Jira.

    Parameters
    ----------
    issue_key
        Issue key, e.g. 'ABC-123'.
    to_status
        Destination status (e.g. 'In Progress') or transition name, in any case.
        Destination status is matched first.
    fields
        Extra fields that the transition screen requires, e.g.
        {'resolution': {'name': 'Done'}}.
    """
    transitions = _request("GET", f"/issue/{issue_key}/transitions").get("transitions", [])
    wanted = to_status.strip().lower()
    match = next(
        (t for t in transitions if _name(t.get("to")).lower() == wanted), None
    ) or next((t for t in transitions if t["name"].lower() == wanted), None)
    if match is None:
        options = [f"{t['name']} -> {_name(t.get('to'))}" for t in transitions]
        raise ToolError(f"No transition to '{to_status}' from here. Available: {options}")
    payload: dict[str, Any] = {"transition": {"id": match["id"]}}
    if fields:
        payload["fields"] = fields
    _request("POST", f"/issue/{issue_key}/transitions", json=payload)
    return {"key": issue_key, "status": _name(match.get("to")), "url": _url(issue_key)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
