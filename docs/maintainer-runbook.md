# Maintainer runbook

Short steps for a maintainer. Read it before the first PR. For the first setup, read `docs/SETUP.md`.

## Roles

- Maintainers: the logins in `.github/CODEOWNERS`. The template maintainer is `@Poxagronka`. `scripts/adapt.py --maintainer` replaces it.
- Authors in the repo variable `REVIEW_AUTHORS` get the Claude review and the auto-approval.
- Other authors wait for a human approval.

## PR flow

1. Open a PR from a branch. Do not push to `main`.
2. Four checks must pass: `gitleaks`, `knowledge`, `meta`, `tools`.
3. The code review sets `claude: approved`. The knowledge review sets `knowledge: approved`.
4. A new push clears both labels. The review runs again on the new head.
5. `auto-approve.yml` approves when the needed labels exist.
6. A docs-only PR skips the code review and the full gate.
7. Merge with `gh pr merge <n> --squash --auto`. Never use an admin merge.

## Local gate before push

```
/opt/homebrew/bin/pytest -q tests
ruff check .
python3.11 scripts/check_knowledge_graph.py
```

After a change under `plugins/knowledge/` outside the vault, run `python3.11 scripts/build_integrity.py`. Then run it with `--check`.

## Release

- A merge to `main` is the release.
- Every user gets it about one hour after the next session start.
- It becomes active on the launch after that.
- Rollback is a revert PR.
- A user can switch features off with the env vars in `README.md`, or uninstall the plugin.

## If approvals break

`claude-review.yml` and `auto-approve.yml` run from `main`. A broken version also judges the fix.

1. Open a revert PR of the bad change.
2. Ask another repo admin to approve it.
3. Merge it. Never admin-merge.

## Secrets

- `ANTHROPIC_API_KEY` is a repo secret. Only the review jobs use it.
- Credentials for the verify run (directory key, Slack token) live in an env file outside the repo, with mode 600. Pass it with `--env-file`.
- Never put a secret in the repo.

## Verify runs and defects

- Run `python scripts/knowledge_verify.py --oldest 60 --out report.json` from a clone.
- It needs the `gh` CLI. See "Self-verification" below for the options.
- Write each defect you find and do not fix to its own file in `defects/`.
- The PR that fixes a defect deletes its file.

## Add a maintainer

1. Give the person write access to the repo.
2. Add the login to the repo variable `REVIEW_AUTHORS` (a JSON array). It covers the review, the auto-approval and the hand label.
3. Add the login to each `.github/CODEOWNERS` line.
4. The person runs `gh auth login` and creates the env file.
5. The person ships one PR end to end.
6. Then turn on "Require review from Code Owners" on `main`.

## Fleet health

- The `fleet-health.yml` workflow runs every Monday. Run it by hand with `gh workflow run fleet-health.yml`.
- The report is in the run summary. Run `python3 scripts/fleet_health.py --days 30` for a local copy.
- The report undercounts. A user without a logged-in `gh` sends nothing.
- A red run means an alert fired. First action per alert:

| Alert | First action |
|---|---|
| No sessions in the window | Check the plugin release and the `telemetry` issues. Ask a teammate to run a session. |
| `ranker_install` failed | Ask the user for `install.log` in the plugin data dir. Fix the installer. |
| Swallowed plugin errors | Find the `<place>:<ExcType>` key in the plugin code. Write a defect file if you do not fix it. |
| Selector p95 above 8000 ms | Check `ranker_install` per user. Check the Haiku timeout. |
- To stop the alert mail, disable the workflow in the Actions tab.

## Warehouse

To add a tool:

1. Open a PR with the tool folder under `tools/`, for example `tools/my_tool/`.
2. In the same PR, add a hub `plugins/knowledge/vault/entities/tool-my-tool.md`. Copy `tool-example-tool.md`. The hub names the path, the entry docs and how to fetch only that folder.
3. Add the hub to `plugins/knowledge/vault/_index.md`.
4. If the tool is a plugin, add it to `.claude-plugin/marketplace.json`. The hub then says `/plugin install my-tool@<marketplace>`.

## Tool contract

CI runs `make check` for each changed tool folder. A tool folder has a `Makefile` with a `check` target.

- `make check` must work on a clean `ubuntu-latest` runner. It has python3, node, go, rust and uv. The target installs its own dependencies.
- Optional: set `CI_SHARDS = N` (1 to 16) to split the work. CI runs one job per shard. It passes the variables `SHARD` (1-based) and `SHARDS`.
- A folder with only markdown files (for example a skill) needs no `Makefile`.
- A folder with code and no `check` target fails the `units` job.

```make
CI_SHARDS = 2

check:
	uv run --with pytest --with pytest-split pytest --splits $(SHARDS) --group $(SHARD)
```

A nested folder can be the unit, for example `skills/<author>/<name>/Makefile`. CI picks the nearest `Makefile` above each changed file.

## Self-verification

Every hub and entry names a live check in `verify_with` and `verify_query`. Run it from a clone of this repo (a plugin install does not ship `scripts/`). It needs Python 3.9 or newer and the `gh` CLI:

```bash
python scripts/knowledge_verify.py --today 2026-10-17 --oldest 60 --out report.json
```

The script checks `github` and `directory` queries. The `directory` check needs the env vars named in `directory.base_env` and `directory.key_env` of `knowledge.json`, in the environment or in `--env-file`. Person hubs with a `slack:` ID are also checked with Slack `users.info` when `SLACK_BOT_TOKEN` (or `SLACK_TOKEN`) is set: a deactivated account gives `gone`. Without a token the Slack check is skipped. Paths with `..`, `?`, `#`, `%` or a leading `/` give `error` before any `gh api` call. The report lists a verdict per record: `ok`, `changed`, `gone`, `manual` or `error`. Add `--apply` to stamp `last_verified` on `ok` records. `--apply` also writes `plugins/knowledge/vault/_verify/<date>.json`. The gate rejects a `verified` or `last_verified` date that this log does not hold. It renders every file first, then writes each one through a temp file. A weekly sweep is specified in `docs/cron-contract.md`. The template does not build it. While you work, the `knowledge` skill runs the cheap check before it relies on a record.

## Review and defects

When a push leaves the code diff unchanged, the review is carried forward. The `Claude Review` workflow compares the `git patch-id --stable` of the diff, without docs, `knowledge/`, `defects/` and `integrity.json`, for the last bot-approved commit and the new head. A match approves the new head with no model run. The knowledge review does the same on the vault diff. A run that ends without a verdict for the head fails the job.

Known defects live in `defects/`, one file per defect. See `defects/README.md`. A change that touches only markdown, so `defects/` too, counts as docs-only in CI.
