# Contributing

This repo is a template. Fixes to the template are welcome through pull requests.

## Before you open a PR

1. Run the local gate from the repo root:

   ```bash
   ruff check .
   python3.11 scripts/check_knowledge_graph.py
   python3.11 scripts/build_integrity.py --check
   pytest -q tests
   ```

2. After a change to plugin code or to `plugins/knowledge/knowledge.json`, run `python3.11 scripts/build_integrity.py` and commit `integrity.json`.
3. Keep Python code stdlib only and compatible with Python 3.9.
4. Write code comments and docs in English. Use short sentences.
5. Keep company names, people names and internal ids out of the repo. The vault holds neutral examples only.

## Vault changes

The vault format is in `plugins/knowledge/vault/_template.md`. Every record needs a live check in `verify_with`. Use the issue form (New issue, then Knowledge proposal) in a repo that runs the plugin.

## Maintainer docs

See `docs/maintainer-runbook.md` and `docs/SETUP.md`.
