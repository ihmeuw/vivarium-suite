---
name: plugin-setup
description: Use when the user is performing post-install configuration for components shipped by this plugin — work that the plugin install itself doesn't perform.
---

# Plugin setup

Some components shipped by this plugin need configuration that the plugin install itself doesn't perform. When the user asks about completing setup for one of the items below, walk them through it.

## Experimental agent teams (for `/simsci:type-hinter`)

The `/simsci:type-hinter` skill runs as an agent team — one autonomous
teammate per file, coordinating directly. Agent teams are an
experimental, opt-in Claude Code feature, and the command has no
single-agent fallback, so enable it before using the command. Two
requirements:

1. **Claude Code v2.1.32 or newer** (`claude --version`; update if older).
2. **`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`** in the environment Claude
   Code launches from. Add it to shell init so every session has it:

```bash
# >>> claude code agent teams >>>
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# <<< claude code agent teams <<<
```

Use `~/.zshenv` (or the shell equivalent — the same file as the Jenkins
credential below), then restart Claude Code. The command's Step 0
preflight confirms both before doing any work.

## Jenkins MCP server

Connect Claude Code to the IHME SimSci Jenkins (`jenkins.simsci.ihme.washington.edu`) via the [Jenkins MCP Server plugin](https://plugins.jenkins.io/mcp-server/) so build status, console logs, and job structure are queryable from chat. The plugin only supports HTTP Basic auth, so the steps below protect a long-lived API token.

### 1. Generate a Jenkins API token

Have the user open `https://jenkins.simsci.ihme.washington.edu/me/configure` (or click their name in the top-right, then **Security**). Under **API Tokens**, they click **Add new token**, name it (e.g. `claude-code-mcp`), and copy the token. Jenkins only shows it once.

Warn the user that this token has the same permissions as their Jenkins user — they should treat it like a password.

### Credential-handling overview (steps 2–4)

The Jenkins MCP plugin only accepts HTTP Basic auth (`Authorization: Basic base64(user:apitoken)`). Steps 2–4 keep that credential secure:

| Step | What it does | Why |
|------|-------------|-----|
| **2** | Write the base64-encoded credential to a `0600` file | Keeps the secret out of Claude config and shell history |
| **3** | Export it as `JENKINS_MCP_AUTH` in `~/.zshenv` | Makes it available to every shell, including Claude Code's |
| **4** | Add the secrets path to the global gitignore | Prevents accidental commits |

### 2. Stash the credential as a 0600 file

Have the user run:

```bash
mkdir -p ~/.claude/secrets && chmod 700 ~/.claude/secrets

# `-s` suppresses echo; paste "yourjenkinsuser:thetoken" and press Enter.
read -rs JENKINS_RAW
printf '%s' "$JENKINS_RAW" | base64 -w0 > ~/.claude/secrets/jenkins-mcp-b64
chmod 600 ~/.claude/secrets/jenkins-mcp-b64
unset JENKINS_RAW
```

### 3. Export from their shell init

Have the user add this to `~/.zshenv` (or their shell's equivalent — `.bash_profile`, `.bashrc`, etc.):

```bash
# >>> jenkins mcp credential >>>
if [ -r "$HOME/.claude/secrets/jenkins-mcp-b64" ]; then
  export JENKINS_MCP_AUTH="$(< "$HOME/.claude/secrets/jenkins-mcp-b64")"
fi
# <<< jenkins mcp credential <<<
```

`~/.zshenv` is sourced for every shell — including the non-interactive shell Claude Code launches from — so the variable is available when `claude` starts.

### 4. Gitignore the secrets path globally

Belt and suspenders against accidental commits:

```bash
mkdir -p ~/.config/git
printf '.claude/secrets/\n' >> ~/.config/git/ignore
```

Git auto-detects `~/.config/git/ignore`; no `core.excludesFile` change is needed.

### 5. Register the MCP server with Claude Code

```bash
claude mcp add --transport http --scope user jenkins \
  https://jenkins.simsci.ihme.washington.edu/mcp-server/mcp \
  -H 'Authorization: Basic ${JENKINS_MCP_AUTH}'
```

The single quotes around the `-H` argument are **load-bearing** — they preserve `${JENKINS_MCP_AUTH}` as a literal string so Claude Code expands it at connect time, rather than the user's shell expanding it at `add` time.

### 6. Verify without leaking the credential

**Do not** suggest `claude mcp get jenkins` — it expands `${JENKINS_MCP_AUTH}` in its output and surfaces the credential in tool results and session logs.

Instead, have the user verify with one of these:

```bash
# Status only, no headers
claude mcp list

# Confirm the on-disk config still holds the literal ${VAR}
python3 -c "import json; v=json.load(open('$HOME/.claude.json'))['mcpServers']['jenkins']['headers']['Authorization']; print('literal' if v.startswith('Basic \${') else 'RESOLVED -- leaked')"
```

Inside Claude Code, `/mcp` shows connection status without dumping headers.

### Sidebar: alternative secret stores (Jenkins)

The 0600-file approach above is the team default — short install, consistent behavior, easy to teach. If a teammate has a strong reason to use something else, the substitution surface in `~/.claude.json` (`${JENKINS_MCP_AUTH}`) doesn't care where the value comes from. Have them point `JENKINS_MCP_AUTH` at it in shell init:

- **`pass`** (GPG-encrypted store): `export JENKINS_MCP_AUTH="$(pass show jenkins/mcp-b64 2>/dev/null)"`. Adds a GPG passphrase prompt on first use per session.
- **`secret-tool`** (libsecret/gnome-keyring): `export JENKINS_MCP_AUTH="$(secret-tool lookup service jenkins-mcp 2>/dev/null)"`. Needs an unlocked keyring daemon — awkward on headless WSL.
- **macOS Keychain**: `export JENKINS_MCP_AUTH="$(security find-generic-password -s jenkins-mcp -w 2>/dev/null)"`. Clean on Mac, prompts the first time per app.

All of these end up at the same state: `JENKINS_MCP_AUTH` set in the environment of whatever shell launches `claude`, and the `.claude.json` entry continues to reference `${JENKINS_MCP_AUTH}` literally.

## Jira MCP server

This plugin ships its own Jira MCP server, `jira` (tools named
`mcp__plugin_simsci-internal_jira__*`). It replaces IHME Infra's hosted
`mcp-jira` plugin for SimSci work, because Infra's server cannot read or set
story points, priority, sprint, or fix versions on create. The server runs
locally and calls Jira with the user's personal access token.

### 1. Install `uv`

Claude Code starts the server with `uv run --script`, which installs the
server's Python dependencies on first use. Check with `uv --version`. If it is
missing, have the user install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Create a Jira personal access token

Have the user open `https://jira.ihme.washington.edu`, click their avatar,
then **Profile** > **Personal Access Tokens** > **Create token**. Jira shows
the token only once. The token has the same permissions as the user's Jira
account, so they should treat it like a password.

### 3. Store the token

The server reads `JIRA_API_TOKEN` from the environment. If the variable is not
set, it reads the line `export JIRA_API_TOKEN=<token>` from `~/.tokens`. Have
the user run this in their own terminal, not through Claude, so the token does
not enter the conversation:

```bash
touch ~/.tokens && chmod 600 ~/.tokens
read -rs JIRA_TOKEN_RAW   # paste the token and press Enter
printf 'export JIRA_API_TOKEN=%s\n' "$JIRA_TOKEN_RAW" >> ~/.tokens
unset JIRA_TOKEN_RAW
```

### 4. Restart and verify

Restart Claude Code and run `/mcp`. The `plugin:simsci-internal:jira` server
must show as connected. Then ask Claude to get any MIC ticket. The result must
include `story_points`. A missing or invalid token gives an error that names
`JIRA_API_TOKEN`.

### 5. Disable Infra's Jira plugin

With both servers enabled, Claude can pick Infra's `create_issue`, which
silently drops story points. Have the user disable it:

```text
/plugin disable mcp-jira@ihme-claude-marketplace
```

### Optional settings

These environment variables change the server's defaults:

| Variable | Default | Use |
|----------|---------|-----|
| `JIRA_PROJECT` | `MIC` | Project for `create_issue` when no `project` is given |
| `JIRA_BOARD_ID` | `301` (MIC board) | Board that sprint names and `active` resolve against |
| `JIRA_BASE_URL` | `https://jira.ihme.washington.edu` | Jira instance |

## GitHub MCP server

The `github` plugin connects Claude Code to GitHub via the hosted MCP server at
`https://api.githubcopilot.com/mcp/`, so PRs, reviews, issues, diffs, and
Actions runs are queryable as tools. You can run **two identities at once** —
pick the server by the repo's org:

| Org | Server | Tools |
|-----|--------|-------|
| `ihmeuw` (+ public repos) | `github` (plugin default) | `mcp__plugin_github_github__*` |
| `ihme-internal` (private) | `github-internal` (you add it) | `mcp__github_internal__*` |

The accounts don't overlap (each 404s on the other's org). Verify with
`claude mcp list` or `/mcp`, never `claude mcp get` (it leaks the token).

**Credential pattern (both servers).** Authenticate each with a `headersHelper`,
not a plain `${VAR}` header — the env var resolves *empty* in agent-team
teammate reconnects (empty bearer → HTTP 400). The helper reads a 0600 token
file that `~/.zshrc` refreshes from `gh auth token`:

```json
"headersHelper": "printf '{\"Authorization\":\"Bearer %s\"}' \"$(cat $HOME/.claude/secrets/<token-file>)\""
```

**Account 1 — `ihmeuw` (`github`).** The plugin ships pre-wired. Generate the
token with `gh auth login` (the default `gh` profile) and SSO-authorize it for
`ihmeuw`; `~/.zshrc` caches `gh auth token` to `~/.claude/secrets/github-token`.
Add the helper above pointing at that file — it lives in the plugin **cache**,
so reapply after a reinstall.

**Account 2 — `ihme-internal` (`github-internal`).** Authenticate with the `gh`
**web OAuth flow, not a fine-grained PAT** (those come back with zero-repo
access). Use an isolated `gh` profile so it doesn't clobber Account 1:

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-ihme"
gh auth login --hostname github.com --web    # authorize SAML SSO for ihme-internal
```

Refresh `~/.claude/secrets/github-internal-token` from that profile's
`gh auth token` in `~/.zshrc`, export `GITHUB_INTERNAL_TOKEN` from it in
`~/.zshenv`, then register the server and add the matching `headersHelper`
(pointing at `github-internal-token`) to its `~/.claude.json` entry:

```bash
claude mcp add --transport http --scope user github-internal \
  'https://api.githubcopilot.com/mcp/?id=internal' \
  -H 'Authorization: Bearer ${GITHUB_INTERNAL_TOKEN}'
```

The `?id=internal` is **required** — Claude Code dedups servers by URL and
silently drops one if two share it. If `github-internal` won't connect, the
account likely lacks the Copilot license the hosted endpoint needs; swap it for
the local `github-mcp-server` binary. Relaunch Claude Code from a fresh shell
after changes so `~/.zshenv` is re-sourced.

### Sandboxed `git push`

The MCP can't push local commits, and plain `git push` also fails under the
sandbox: git's `github.com` credential helper is `gh` (which can't read its
denied config), and `github.com` egress isn't allowlisted by default. Fix
both without un-denying anything — point git's `github.com` credential helper
at the same `~/.claude/secrets/` token file (appended *after* `gh`, so it's
only the sandboxed fallback), and add `github.com` to
`sandbox.network.allowedDomains` (see the README's "Recommended sandbox
configuration"). An in-sandbox `git ls-remote` against a private repo
confirms both halves.

## Brainstorming visual companion (Node.js)

The `brainstorming` skill ships a browser-based visual companion that renders Mermaid diagrams. Its server is written in Node.js. Without Node, the brainstorming skill still works — just no live diagrams.

Install Node via [nvm](https://github.com/nvm-sh/nvm) and verify (`node --version` should print a version on the shell that launches `claude`). The brainstorming skill handles `start-server.sh` and `stop-server.sh` itself when it offers the companion.
