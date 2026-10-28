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

The `simsci` plugin, which this plugin depends on, ships a Jira MCP server,
`jira` (tools named `mcp__plugin_simsci_jira__*`). This plugin's skills and
agents use it to read and file MIC tickets. It runs locally and calls Jira with
the user's personal access token.

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

### 3. Enter the token in the `simsci` plugin configuration

Claude Code asks for `simsci`'s options when the plugin is enabled. If the user
skipped that dialog, or needs to change the token later, have them run
`/plugin`, open **Installed** > **Simulation Science Dev AI Tools** (the
display name of `simsci`), and choose **Configure options**. A session that was
open when the plugin was installed or updated does not show **Configure
options** until the user runs `/reload-plugins` or restarts Claude Code.
They paste the token into **Jira personal access token**. Claude Code masks the
field and keeps the value in its credential store, not in `settings.json`. On
Linux, that store is `~/.claude/.credentials.json`, which only the user can read. The user must type the token into the dialog themselves, so it
does not enter the conversation.

The same dialog has an optional **Default Jira project**. SimSci users can set
it to `MIC`. This plugin's skills pass `project="MIC"` explicitly, so they work
without it.

### 4. Restart and verify

Restart Claude Code and run `/mcp`. The `plugin:simsci:jira` server must show
as connected. If it shows `Executable not found in $PATH: "uv"`, `uv` is not on
the `PATH` of the shell that starts `claude`. Fix that (step 1), then restart.
Until the server connects, Claude has no Jira tools and gets no error that
names the cause. Claude Code remembers a failed connection for up to 15
minutes, also across restarts. If `/mcp` then says that a recent failure is
cached, wait until the 15 minutes have passed and restart again. Then ask Claude to get any MIC ticket. The result must include
`story_points`. If the token is missing, the Jira tools return an error that
points to **Configure options**.

### 5. Disable the `mcp-jira` plugin, if installed

If the user has the `mcp-jira` plugin from `ihme-claude-marketplace`
installed, have them disable it, so that Claude uses the `simsci` Jira tools:

```text
/plugin disable mcp-jira@ihme-claude-marketplace
```

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
