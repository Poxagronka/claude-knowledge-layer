# Step 6: Roll out to your team

This step puts the plugin on the machines of your team. The plugin installs at user scope and works in the terminal, the IDE extensions and the Code tab of the Claude desktop app.

Contents: [Before you roll out](#before-you-roll-out), [Install commands](#install-commands), [Install with Claude](#install-with-claude), [Permissions allowlist](#permissions-allowlist), [Team settings](#team-settings), [Check it](#check-it), [Tell the team](#tell-the-team).

## Before you roll out

1. The repo is pushed and the plugin repo is readable by every teammate. A private repo needs repo access for each person.
2. `plugins/knowledge/knowledge.json` has your values. `integrity.json` is current (`python3 scripts/build_integrity.py --check`).
3. The issues repo, labels and access exist (see [issues.md](issues.md)).
4. One person tested the install on a clean machine or a clean user account.

## Install commands

A teammate needs `claude`, `gh` and `python3` 3.9 or newer. For a private repo, log in and set up git credentials first:

```bash
gh auth login
gh auth setup-git
```

Install the plugin. Replace `<owner>/<repo>` with the plugin repo and `<marketplace>` with the `marketplace` value in `knowledge.json`:

```bash
claude plugin marketplace add <owner>/<repo> --sparse .claude-plugin
claude plugin install knowledge@<marketplace>
```

The sparse checkout downloads only the marketplace file and the plugin folder. It skips the tool warehouse.

Start a new session. The header reads `# <org> knowledge vault`, names the vault path and shows the vault index.

## Install with Claude

A teammate can paste this prompt into a Claude Code session. Fill the three values in angle brackets before you share it.

```text
Install the knowledge plugin for Claude Code. Do the steps in order and stop at the first failure.
1. Check the tools: `claude --version`, `gh --version`, `python3 --version` (3.9 or newer). If one is missing, tell me how to install it and stop.
2. Check GitHub: `gh auth status`. If I am not logged in, ask me to run `! gh auth login` in the prompt, and wait. Never print or ask for a token.
3. Check access: `gh repo view <owner>/<repo> --json name`. If it fails, tell me to ask the team owner for access, and stop.
4. Run `gh auth setup-git`.
5. Run `claude plugin marketplace add <owner>/<repo> --sparse .claude-plugin`. If the marketplace already exists, continue.
6. Run `claude plugin install knowledge@<marketplace>`.
7. Run `claude plugin list` and confirm that `knowledge@<marketplace>` shows with scope user.
8. Tell me to start a new session. Its header must name the vault path and show the vault index.
```

## Permissions allowlist

The capture worker files issues with its own process. It needs no rule. The rule below is for your main session. It lets Claude run `gh issue create` and `gh issue list` on the issues repo, without a prompt, when you ask it to file a proposal.

Add it to `~/.claude/settings.json` (or to the shared settings file of your team). Replace `<owner>/<repo>` with `issues_repo`:

```json
{"permissions": {"allow": [
  "Bash(gh issue create -R <owner>/<repo>:*)",
  "Bash(gh issue list -R <owner>/<repo>:*)"
]}}
```

Keep the rule narrow. It names one repo and two commands. Do not allow `gh` in general.

## Team settings

Claude Code can pre-register a marketplace through a settings file. A third-party marketplace does not auto-update by default. Set `"autoUpdate": true` on its entry, or users keep the old version. The plugin also updates itself in the background at most once an hour, so this is optional. Check the Claude Code settings documentation for the exact keys of your version before you ship a managed settings file.

## Check it

On the machine of a teammate:

1. `claude plugin list` shows `knowledge@<marketplace>` with scope user.
2. A new session header names the vault and your organization.
3. A question about a hub in your vault gets an answer with a citation `vault/<file>#^id`.
4. After a session with vault use, a `telemetry: <login>` issue exists in the issues repo.
5. The plugin data folder holds `capture/worker.log` after a long session.

## Tell the team

Share a short note. It should say:

- What the plugin does and that it is the shared knowledge vault.
- That Claude reads vault entries and that the selector sends the prompt and candidate entries to Claude Haiku.
- That the plugin files issues to the private issues repo, and what telemetry holds.
- The opt-out variables: `KNOWLEDGE_TELEMETRY=0`, `KNOWLEDGE_CAPTURE=0`, `KNOWLEDGE_SELECT=0`, `KNOWLEDGE_AUTOUPDATE=0`. The local ranker is opt-in: `KNOWLEDGE_LOCAL=1` turns it on.
- How to propose a fix: the issue form, or ask Claude to file a proposal.
- Where to report a problem with the plugin.

Updates need no action. A merge to `main` ships to everyone about one hour after their next session start, and becomes active on the launch after that. A rollback is a revert pull request.
