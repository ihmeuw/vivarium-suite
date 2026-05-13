---
name: plugin-setup
description: Use when the user is performing post-install configuration for components shipped by this plugin — work that the plugin install itself doesn't perform.
---

# Plugin setup

Some components shipped by this plugin need configuration that the plugin install itself doesn't perform. When the user asks about completing setup for one of the items below, walk them through it.

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

### Sidebar: alternative secret stores

The 0600-file approach above is the team default — short install, consistent behavior, easy to teach. If a teammate has a strong reason to use something else, the substitution surface in `~/.claude.json` (`${JENKINS_MCP_AUTH}`) doesn't care where the value comes from. Have them point `JENKINS_MCP_AUTH` at it in shell init:

- **`pass`** (GPG-encrypted store): `export JENKINS_MCP_AUTH="$(pass show jenkins/mcp-b64 2>/dev/null)"`. Adds a GPG passphrase prompt on first use per session.
- **`secret-tool`** (libsecret/gnome-keyring): `export JENKINS_MCP_AUTH="$(secret-tool lookup service jenkins-mcp 2>/dev/null)"`. Needs an unlocked keyring daemon — awkward on headless WSL.
- **macOS Keychain**: `export JENKINS_MCP_AUTH="$(security find-generic-password -s jenkins-mcp -w 2>/dev/null)"`. Clean on Mac, prompts the first time per app.

All of these end up at the same state: `JENKINS_MCP_AUTH` set in the environment of whatever shell launches `claude`, and the `.claude.json` entry continues to reference `${JENKINS_MCP_AUTH}` literally.
