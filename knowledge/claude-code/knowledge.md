---
id: claude-code/knowledge
type: fact
tags: [domain/claude-code]
---
# Claude Code facts

## Hooks

- 2026-10-17: **A plugin's SessionEnd hooks share a 1.5 s budget, and a plugin cannot raise it.** A detached child survives the exit of `claude -p`. Start it with `start_new_session=True` and stdio to DEVNULL. Verified live: the telemetry comment landed. Use this for slow work at session end.
  [added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] ^K-sessionend-budget
- 2026-10-17: **PostToolUse `tool_response` for Grep is a dict.** Example: `{"mode":"content","numFiles":0,"filenames":[],"content":"","numLines":0}`. Zero matches means `numFiles == 0`. Observed live and not documented, so re-check after a Claude Code update.
  [added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] ^K-grep-tool-response
- 2026-10-17: **SessionStart plain stdout is added to the context, capped at 10,000 characters.** Keep the hook output below the cap.
  [added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] ^K-sessionstart-stdout-cap

## Marketplaces

- 2026-10-17: **Third-party marketplaces do not auto-update by default.** Set `"autoUpdate": true` on the `extraKnownMarketplaces` entry, or use `/plugin` then Marketplaces. Without it, users keep the old plugin version.
  [added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] ^K-marketplace-autoupdate
