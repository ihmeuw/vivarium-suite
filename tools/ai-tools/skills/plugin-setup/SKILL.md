---
name: plugin-setup
description: Use when the user is performing post-install configuration for components shipped by this plugin — work that the plugin install itself doesn't perform.
---

# Plugin setup

Some components shipped by this plugin need configuration that the plugin install itself doesn't perform. When the user asks about completing setup for one of the items below, walk them through it.

## Experimental agent teams (for `/viv:type-hinter`)

The `/viv:type-hinter` command runs as an agent team — one autonomous
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

## GitHub MCP server

The `github` plugin (a dependency of this plugin) connects Claude Code to
GitHub via the official hosted MCP server at
`https://api.githubcopilot.com/mcp/`, so PRs, reviews, issues, diffs, and
Actions runs are queryable as tools. Prefer it over the `gh` CLI: MCP tool
calls run **outside** the Bash sandbox, so they work where `gh` cannot —
the project sandbox `denyRead`s `~/.config/gh/hosts.yml`, which breaks `gh`
in any sandboxed command. The one thing the MCP cannot do is push a local
commit graph; keep `git push` for that.

The plugin ships an `http` server whose auth is a single header,
`Authorization: Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}`. Two problems make
the bare env-var substitution unreliable, so we replace it with a
`headersHelper` that reads a secret file — the same 0600-file pattern as
the Jenkins credential above.

**Why the bare env var fails:** with experimental agent teams
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), opening the agents view triggers
an MCP reconnect in a teammate context that does **not** inherit
`GITHUB_PERSONAL_ACCESS_TOKEN`. The header then resolves to an empty
`Bearer `, and the endpoint returns `HTTP 400: Authorization header is
badly formatted` — surfacing as a `/mcp` connection failure. Plain `claude`
works (its MCP server inherits the lead process's env), so the bug only
appears once you use teams. A `headersHelper` avoids this because Claude
Code runs it fresh on every connection — including the teammate reconnect.

### 1. Stash the token as a 0600 file, refreshed from `gh`

The helper reads a cache file rather than calling `gh` directly, because
`gh` cannot run in a sandboxed context (it needs `~/.config/gh/hosts.yml`,
which the sandbox denies). Refresh the file from `gh auth token` on each
interactive shell. Have the user add this to `~/.zshrc` (interactive shells
— mirrors the Jenkins file pattern in `~/.zshenv`):

```bash
# >>> github mcp token >>>
mkdir -p "$HOME/.claude/secrets" && chmod 700 "$HOME/.claude/secrets"
if command -v gh >/dev/null 2>&1; then
  gh auth token > "$HOME/.claude/secrets/github-token" 2>/dev/null \
    && chmod 600 "$HOME/.claude/secrets/github-token"
fi
# <<< github mcp token <<<
```

Using the `gh` token reuses a credential already SAML-SSO-authorized for
the `ihmeuw` org — the same one that lets `git push` work.

### 2. Point the plugin's MCP config at the helper

Edit the github server's `.mcp.json` (in the plugin **cache**:
`~/.claude/plugins/cache/claude-plugins-official/github/unknown/.mcp.json`),
replacing the `Authorization` header with a `headersHelper`:

```json
{
  "github": {
    "type": "http",
    "url": "https://api.githubcopilot.com/mcp/",
    "headersHelper": "printf '{\"Authorization\":\"Bearer %s\"}' \"$(cat $HOME/.claude/secrets/github-token)\""
  }
}
```

The `$(cat ...)` strips the file's trailing newline so the header isn't
malformed. Restart Claude Code, then confirm with `claude mcp list` (look
for `github … ✔ Connected`) — **not** `claude mcp get`, which would expand
and leak the token.

**Caveats.** (1) `.mcp.json` lives in the plugin cache, so a github plugin
update or reinstall overwrites it — reapply the `headersHelper` line after
any reinstall. (2) The token is now in a file any sandboxed command can
read (the same exposure already accepted for the Jenkins credential). (3)
If the `gh` token rotates and no interactive shell has refreshed the file,
it can go stale — fine for static PATs.

## Brainstorming visual companion (Node.js)

The `brainstorming` skill ships a browser-based visual companion that renders Mermaid diagrams. Its server is written in Node.js. Without Node, the brainstorming skill still works — just no live diagrams.

Install Node via [nvm](https://github.com/nvm-sh/nvm) and verify (`node --version` should print a version on the shell that launches `claude`). The brainstorming skill handles `start-server.sh` and `stop-server.sh` itself when it offers the companion.
