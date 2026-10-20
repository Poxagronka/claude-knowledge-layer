---
id: claude-code/rules
type: rule
tags: [domain/claude-code]
---
# Claude Code rules

## R1 — Use a full HTTPS URL for a git-subdir plugin source
A `git-subdir` plugin source with the `owner/repo` shorthand clones over SSH. It has no HTTPS fallback. Install fails with `Permission denied (publickey)` for anyone without a GitHub SSH key.
Write the full URL `https://github.com/<owner>/<repo>.git` instead. The `gh auth setup-git` credential helper covers it.
[added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] [status:: active] ^R1

## R2 — Add a large monorepo marketplace with a sparse checkout
Run `claude plugin marketplace add <repo> --sparse .claude-plugin` and use `git-subdir` plugin sources. Users then get only the marketplace file and the plugin folder.
[added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] [status:: active] ^R2
