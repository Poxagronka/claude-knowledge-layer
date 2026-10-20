# Open defects

Known defects that nobody fixes yet. Add a file when you find one. Delete the file in the PR that fixes it.

One file per defect, so parallel PRs never conflict: `defects/<area>-<slug>.md`.

Format: frontmatter with `section` (for example `Plugin`, `Verification scripts`, `CI and repo` or `Not built yet`) and `found` (date). The body is one line: `**<area>: <defect>.** <effect>. <where>. Found <date>.`

Example file `defects/<area>-<slug>.md`:

```markdown
---
section: CI and repo
found: 2026-01-15
---
**<area>: <defect in one sentence>.** <effect for the user>. <path or script>. Found 2026-01-15.
```
