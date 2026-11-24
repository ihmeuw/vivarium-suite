---
name: _test-summary-refresh
description: "Internal building block: bring the per-file test summary comments on an open PR up to date with its current head. The plugin's push hook asks for it after Claude pushes to a branch with an open PR. `_finalize-core` Step 5 follows its posting rules. Not an entry point."
allowed-tools: Read, Grep, Glob, Bash, Agent(simsci:_test_summarizer)
user-invocable: false
---

Refresh the test summary comments on a pull request: $ARGUMENTS

The summary is one comment per changed source file. Each comment maps the cases
that file must handle to the tests that cover them.

## Step 1 — Find the PR and check the working tree

1. Find the open PR for the current branch (`gh pr view --json number,state,baseRefName,headRefOid`).
   If there is none, or it is closed or merged, say so and stop.
2. Fetch, then check that the tracked files in the working tree match the PR
   head: `git diff --quiet <head SHA>`. The summarizer reads the working tree,
   so it must match the pushed head. After a GitHub MCP push, the files match
   but the local `HEAD` is behind. That is fine, so do not pull. If the files do
   not match, the branch is only behind, and `git status --porcelain` shows no
   changes to tracked files, run `git pull --ff-only`. In any other case, stop
   and say what differs. Never stash, reset, or check out for the user.

## Step 2 — Run the summarizer

Dispatch `simsci:_test_summarizer` with:

- the diff `git diff origin/<base>...<head SHA>`. The three dots diff from the
  merge-base, as GitHub does, so changes merged in from the base stay out,
- the changed-file list (`git diff --name-status origin/<base>...<head SHA>`),
- the repo's GitHub URL as the link base.

## Step 3 — Post with the rules below

Apply the posting rules in the next section. Then report the PR number, what you
edited, deleted, and posted, each file's summary line, and every ``Not tested``
row.

## Posting rules

**Marker.** Start every summary comment with a hidden marker on its own line:
`<!-- test-summary: <path> -->`. A summary split into parts uses
`<!-- test-summary: <path> part N -->`. A summary comment is a comment that has
the marker and that the current `gh` user wrote (`gh api user --jq .login`).
A comment by the current `gh` user that starts with **Test summary for** and has
no marker is an older summary. Treat it as a summary comment, so the rules edit
or delete it. Never touch any other comment.

**Body.** Remove the summarizer's ``## File: <path>`` line, and replace each
``{sha}`` with the PR head SHA.

**Find the existing summaries.** List the PR's review threads with their current
path and state:

```
gh api graphql -f query='query($o:String!,$n:String!,$pr:Int!){repository(owner:$o,name:$n){
  pullRequest(number:$pr){reviewThreads(first:100){nodes{path isOutdated
  comments(first:50){nodes{databaseId author{login} body}}}}}}}' -f o=<owner> -f n=<repo> -F pr=<number>
```

Match a summary to a file by the thread's `path`, not by the path in the marker.
GitHub moves a thread to the new path when a file is renamed.

**What to do with each file.**

| File in the PR diff | Summary comment | Action |
|---|---|---|
| no | any | Delete it |
| yes | none | Post a new one |
| yes | current thread | Edit the body if it changed |
| yes | outdated thread | Delete it, then post a new one |

A file-level comment stays current through force-pushes and renames while its
file is in the diff. It becomes outdated when the file leaves the diff, and it
stays outdated if the file comes back. Delete any second summary on the same
file.

**API calls.**

- Edit: `gh api -X PATCH repos/<owner>/<repo>/pulls/comments/<id> -f body=<body>`
- Delete: `gh api -X DELETE repos/<owner>/<repo>/pulls/comments/<id>`
- Post: with the GitHub MCP, open one pending review, add every new file comment
  to it, and submit it with the event ``COMMENT``, so reviewers get one
  notification. Otherwise, post each with
  `gh api repos/<owner>/<repo>/pulls/<number>/comments` and the fields `path`,
  `commit_id` (the head SHA), `subject_type=file`, and `body`.

**Limits.**

- Post only on files in the PR diff. GitHub rejects other paths with HTTP 422.
- GitHub rejects a comment longer than 65,536 characters. Split a longer table
  across comments on that file, repeat the table header in each, and number the
  markers as parts.
- Never refresh a closed or merged PR.
- Post the summary as the summarizer returned it. Never add coverage it did not
  find, and never remove a ``Not tested`` row.
- Do not edit, commit, or push files. This skill only reads the branch and writes
  PR comments.
