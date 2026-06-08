---
name: team-conventions
description: SimSci Engineering team conventions for everyday git/Jira/PR mechanics — branch naming from a MIC ticket, writing a Jira ticket against the team's hub doc, opening a PR with the repo's `.github/pull_request_template.md` via the GitHub MCP, and flagging the PR for team review in `#vivarium_dev`. Use whenever the user asks to "make a branch", "name this branch", "create a ticket", "write a Jira ticket", "open a PR", "flag a PR for review", "post my PR", or anything similar where the team convention is the answer.
---

# SimSci Engineering team conventions

The four workflows below are the team-standard ways to start a change, file the ticket that justifies it, ship the resulting PR, and flag it for review. Follow them exactly — drift here makes branches, tickets, and PRs harder to cross-reference in tooling later.

## 1. Naming a branch

Format: `<username>/<library>/mic-####/short-desc`

- `<username>`: the user's active git config user name.
- `<library>`: the monorepo sub-tree being changed, lower-case (e.g. `config-tree` or `ai-tools`, not `Config-tree` or `AI-Tools`). If the change is happening outside the monorepo,
this section can be omitted — e.g. `user/mic-XXXX/desc`
- `mic-####`: the Jira ticket key, lower-case (`mic-6973`, not `MIC-6973`).
- `short-desc`: 2–5 hyphenated words summarizing the change.

Examples that match: `pnast/public-health/mic-6973/team-conventions`, `sbachmei/config-tree/mic-7010/iter`.

Special cases:
- **Epics or non-ticketed work:** `epic/<short-desc>` (e.g. `epic/monorepo`). Use this only when the branch is the long-lived integration target for an epic, *not* for individual sub-tickets that land on it.
- **Release branches:** `release-candidate-x.x.x` (e.g. `release-candidate-1.2.0`). Used by the release flow, not for everyday changes.
- When in doubt, explicitly ask the user how to structure the branch name rather than guessing. In particular, if the user doesn't specify a ticket, ask if they want to file one first (see §2) and use that for the branch name.

Edge cases:
- **No MIC ticket yet.** Ask the user to file one first (see §2). Branches without a ticket are hard to track in sprint reporting.
- **Multiple tickets.** Pick the primary one for the branch name; reference the others in the PR body.
- **Existing branch with the wrong name.** Rename with `git branch -m <new>` before pushing. If already pushed, rename then `git push -u origin <new>` and delete the old remote with `git push origin --delete <old>` — but only with explicit user confirmation, since the old name may be referenced in PRs or CI.

## 2. Writing a Jira ticket

The team's canonical "what belongs on a ticket" doc lives on the IHME hub (Confluence).

Pull it via the hub MCP server when the user asks to write a ticket:

```
mcp__plugin_mcp-hub_mcp-hub__get_page(page_id="178128092")
```

That page (title: *Make a Jira Ticket*, space: SSE) lists the required and optional sections, where to file the ticket, and when ticket creation is expected. Read it, then draft the ticket body using its structure verbatim — overview, acceptance criteria, and the optional fields when they apply. Show the user the draft for review before creating the ticket.

Format the draft in **Jira wiki markup** — not Markdown, not RST.

Once the user approves the draft, create the ticket via the Jira MCP:

```
mcp__plugin_mcp-jira_mcp-jira__create_issue(project="MIC", summary="<title>", description="<wiki-markup body>", issue_type="<Task|Bug|Story|...>")
```

Report the resulting `MIC-####` key back to the user so it can flow into the branch name (§1) and PR body (§3).

If the hub MCP fetch fails, tell the user and link them to `https://hub.ihme.washington.edu/spaces/SSE/pages/178128092/Make+a+Jira+Ticket` — do not improvise a substitute structure from memory.

## 3. Submitting a pull request

Use the GitHub MCP `create_pull_request` tool and the repo's PR template — not a hand-written PR body, and not the `gh` CLI (which can't run in a sandboxed context, since the sandbox denies its credential file).

1. Confirm the PR template exists: `.github/pull_request_template.md` at the repo root (or `tools/ai-tools/.github/...` for the plugin sub-tree). If it doesn't exist, fall back to the repo's `CONTRIBUTING.md` or ask the user — don't invent a template.
2. Read the template's section headings (e.g. *Title*, *Description*, *Category*, *JIRA issue*, *Changes and notes*, *Testing*). Fill each one based on the actual diff, not on what the section heading sounds like — the HTML comments in the template are field-specific instructions (character limits, category enums) that must be followed.
3. **Push the branch first.** The GitHub MCP opens a PR between refs that already exist on the remote — it cannot push a local commit graph. So push the branch with `git push -u origin <branch>` (this one step still uses git, not the MCP), then call `mcp__github__create_pull_request` with `head` = your branch, `base` = `main`, the `title` (imperative summary, ≤50 chars, no trailing period), and the templated `body`:

```
### Description
- *Category*: feature
- *JIRA issue*: https://jira.ihme.washington.edu/browse/MIC-####

### Changes and notes
...

### Testing
...
```

4. If the GitHub MCP is unavailable, `gh pr create --title ... --body "$(cat <<'EOF' ... EOF)"` is the fallback — it bundles the push and PR in one step — but it only works outside the sandbox.

## 4. Flagging a PR for review

A Slack message in `#vivarium_dev` (private channel, ID `GCF5T9TDM`) is the team's primary signal that a PR is ready for review. Open the PR (§3) first, then post.

Format: `<short description> PR <github-link>` — e.g. `AI Tools Team Conventions PR https://github.com/ihmeuw/vivarium-suite/pull/41`. Keep the description to a handful of words.