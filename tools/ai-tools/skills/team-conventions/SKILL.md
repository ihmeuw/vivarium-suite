---
name: team-conventions
description: SimSci Engineering team conventions for everyday git/Jira/PR mechanics — branch naming from a MIC ticket, drafting a Jira ticket against the team's hub doc, opening a PR with the repo's `.github/pull_request_template.md` via `gh`, and flagging the PR for team review in `#vivarium_dev`. Use whenever the user asks to "make a branch", "name this branch", "create a ticket", "draft a Jira ticket", "open a PR", "flag a PR for review", "post my PR", or anything similar where the team convention is the answer.
---

# SimSci Engineering team conventions

The four workflows below are the team-standard ways to start a change, file the ticket that justifies it, ship the resulting PR, and flag it for review. Follow them exactly — drift here makes branches, tickets, and PRs harder to cross-reference in tooling later.

## 1. Naming a branch

Format: `<username>/<type>/mic-####-short-desc`

- `<username>`: the user's active git config user name.
- `<type>`: one of `feature`, `refactor`, `bugfix`, `hotfix`. Pick `feature` for net-new functionality, `refactor` for behavior-preserving changes, `bugfix` for fixes tracked under a MIC ticket, `hotfix` for urgent fixes that bypass the usual sprint flow.
- `mic-####`: the Jira ticket key, lower-case (`mic-6973`, not `MIC-6973`).
- `short-desc`: 2–5 hyphenated words summarizing the change. 

Examples that match: `pnast/feature/mic-6973-team-conventions`, `sbachmei/bugfix/mic-7010-config-tree-iter`.

Special case — epics or non-ticketed work: `epic/<short-desc>` (e.g. `epic/monorepo`). Use this only when the branch is the long-lived integration target for an epic, *not* for individual sub-tickets that land on it.

Edge cases:
- **No MIC ticket yet.** Ask the user to file one first (see §2). Branches without a ticket are hard to track in sprint reporting.
- **Multiple tickets.** Pick the primary one for the branch name; reference the others in the PR body.
- **Existing branch with the wrong name.** Rename with `git branch -m <new>` before pushing. If already pushed, rename then `git push -u origin <new>` and delete the old remote with `git push origin --delete <old>` — but only with explicit user confirmation, since the old name may be referenced in PRs or CI.

## 2. Drafting a Jira ticket

The team's canonical "what belongs on a ticket" doc lives on the IHME hub (Confluence).

Pull it via the hub MCP server when the user asks to draft a ticket:

```
mcp__plugin_mcp-hub_mcp-hub__get_page(page_id="178128092")
```

That page (title: *Make a Jira Ticket*, space: SSE) lists the required and optional sections, where to file the ticket, and when ticket creation is expected. Read it, then draft the ticket body using its structure verbatim — overview, acceptance criteria, and the optional fields when they apply. Show the user the draft for review before they paste it into Jira; this skill does not create Jira tickets directly.

If the MCP fetch fails, tell the user and link them to `https://hub.ihme.washington.edu/spaces/SSE/pages/178128092/Make+a+Jira+Ticket` — do not improvise a substitute structure from memory.

## 3. Submitting a pull request

Use `gh pr create` and the repo's PR template — not a hand-written PR body.

1. Confirm the PR template exists: `.github/pull_request_template.md` at the repo root (or `tools/ai-tools/.github/...` for the plugin sub-tree). If it doesn't exist, fall back to the repo's `CONTRIBUTING.md` or ask the user — don't invent a template.
2. Read the template's section headings (e.g. *Title*, *Description*, *Category*, *JIRA issue*, *Changes and notes*, *Testing*). Fill each one based on the actual diff, not on what the section heading sounds like — the HTML comments in the template are field-specific instructions (character limits, category enums) that must be followed.
3. Pass the body to `gh pr create` via a HEREDOC so multi-line formatting survives:

```bash
gh pr create --title "<imperative summary, ≤50 chars, no trailing period>" --body "$(cat <<'EOF'
### Description
- *Category*: feature
- *JIRA issue*: https://jira.ihme.washington.edu/browse/MIC-####

### Changes and notes
...

### Testing
...
EOF
)"
```

4. Don't push first and then PR — `gh pr create` will push the current branch if needed and prompt for the upstream. Letting it handle the push keeps the tracking ref consistent.

## 4. Flagging a PR for review

A Slack message in `#vivarium_dev` (private channel, ID `GCF5T9TDM`) is the team's primary signal that a PR is ready for review. Open the PR (§3) first, then post.

Format: `<short description> PR <github-link>` — e.g. `AI Tools Team Conventions PR https://github.com/ihmeuw/vivarium-suite/pull/41`. Keep the description to a handful of words; the link does the heavy lifting.

Default flow: stage a draft first, show the user, send on their confirmation.

```
mcp__plugin_slack_slack__slack_send_message_draft(channel_id="GCF5T9TDM", message="<short desc> PR <link>")
# user reviews in Slack, then:
mcp__plugin_slack_slack__slack_send_message(channel_id="GCF5T9TDM", message="<same text>", draft_id="<id from draft>")
```

Skip the draft step only when the user has pre-approved direct posting for this PR. Posting to `#vivarium_dev` is visible to the whole team, so don't send without an explicit go-ahead.