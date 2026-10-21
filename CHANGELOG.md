# Changelog

All notable changes to this project are in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- The repo catalog writes one vault file per repo, `repos/<name>.md` with `type: repo`, plus the map `repos/_index.md`, in place of one `repos/<org>.md` file per org. `scripts/repo_catalog.py` creates files for new repos, deletes files of repos that left the window and never rewrites an existing body.
- The gate checks repo files: `org` (a key of `scripts/repo_catalog.json`), `section`, a verify field, an `entities/` hub link and a 2500 byte cap. The `catalog` file type is removed.
- The selector treats a repo file as a hub record and matches its full name. `scripts/knowledge_verify.py` checks repo files like hubs.

### Upgrading

- An old `repos/<org>.md` catalog file now fails the gate. Copy its curated purpose lines into `repos/<name>.md` files before the next `scripts/repo_catalog.py` run. That run deletes the old file.

## [0.2.0]

### Added

- Windows support for the plugin: Windows file locking, path separators, CRLF-safe hashing, PowerShell read tracking and `python3 -X utf8` for every child process.
- Exec-form hooks (`command` plus `args`). Claude Code 2.1.139 or newer is required.
- `SubagentStart` hook and `PostToolUse` tool hubs. A tool call that names a store id, a bundle id or an alias injects the matching hub. `tool-*` hubs describe skills and tools, and `tests/test_tool_catalog.py` enforces them.
- Local ranker for Windows x64 (`requirements-windows.txt`).
- Telemetry schema 11: `select_paused`, `haiku_ms`, `tool` counts in `injected`, and failure reasons in `errors`.
- `scripts/telemetry_report.py`, `scripts/ci_hook_smoke.py` and `scripts/ci_ranker_smoke.py`.
- CI workflows `plugin-matrix.yml` (Linux, Windows and macOS on x64 and arm64) and `ranker-windows.yml`.
- `SECURITY.md`, `CODE_OF_CONDUCT.md`, a pull request template and a bug report form.

### Changed

- The knowledge selector is pure Haiku. Haiku rates the full vault catalog on every OS.
- The local ranker is opt-in with `KNOWLEDGE_LOCAL=1`. It is no longer on by default.
- The error counts in telemetry are taken by one send, so a count appears in at most one payload.
- `scripts/fleet_health.py` keeps each start of a resumed session.
- `scripts/check_knowledge_graph.py` uses `/` in vault paths on every OS.

## [0.1.0]

### Added

- Initial template: the `knowledge` plugin, the vault format with an example vault, the graph gate, the CI for pull requests, the adapt script and the setup guides.

[Unreleased]: https://github.com/Poxagronka/claude-knowledge-layer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Poxagronka/claude-knowledge-layer/releases/tag/v0.2.0
[0.1.0]: https://github.com/Poxagronka/claude-knowledge-layer/releases/tag/v0.1.0
