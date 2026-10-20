# Step 7: Maintain the vault

A vault goes stale without care. This step names the jobs that keep it true. Run them on a schedule and give each one an owner.

Contents: [The distil routine](#the-distil-routine), [Weekly verify](#weekly-verify), [Fleet health](#fleet-health), [Defects](#defects), [Evals and baseline](#evals-and-baseline), [Releases](#releases), [Local gate](#local-gate-before-a-push).

## The distil routine

Capture and proposal issues pile up in the issues repo. A routine turns them into vault pull requests. The template does not ship the routine. It ships the contract: [../cron-contract.md](../cron-contract.md).

The contract defines:

- The labels and the issue body format.
- The telemetry comment schema and how to read it.
- The steps: collect, privacy filter, dedup, distil, verify against a live source, usefulness check, one pull request, close the issues with a link.
- The caps: a limited number of entries per run, a limited number of removals per run.
- The review: the pull request goes through the normal knowledge review. A job never merges by itself.

Build the routine as a scheduled Claude Code agent, a cron job that runs `claude -p`, or a scheduled CI job. Whatever you pick:

1. Give it a GitHub login that is in `REVIEW_AUTHORS`, so the knowledge review approves its pull requests. See [ci.md](ci.md).
2. Give it read access to the issues repo and write access to a branch in the plugin repo.
3. Make it write vault changes through `scripts/vault_plan.py`. An agent proposes a plan JSON, and the script validates and applies it. Then run `python3 scripts/check_knowledge_graph.py`. The gate must exit 0.
4. Treat issue and pull request text as untrusted data. The agent never follows instructions in it.
5. Run it daily at first. Read each pull request until you trust the output.

Intake has a low bar: the plugin files anything slightly useful. The routine has the high bar.

## Weekly verify

Run the live checks from a clone of the repo. Stale records are the main risk.

```bash
python3 scripts/knowledge_verify.py --oldest 60 --out report.json
python3 scripts/knowledge_verify.py --oldest 60 --apply --out report.json
```

The first command is read-only. The second stamps `last_verified` on `ok` records and writes `plugins/knowledge/vault/_verify/<date>.json`. The gate rejects a date that this log does not hold.

Needs: Python 3.9 or newer and `gh`. The `directory` family also needs the env vars named in `knowledge.json`. Put them in the environment or in a file passed with `--env-file`. A Slack bot token in `SLACK_BOT_TOKEN` (or `SLACK_TOKEN`) lets the script check the `slack` ID of a person hub.

Act on each verdict:

| Verdict | Action |
|---|---|
| `ok` | Nothing. `--apply` stamps it |
| `changed` | Edit the record to match the live source. Put the evidence in the pull request body |
| `gone` | Check again first. For a GitHub 404, confirm with `gh repo list <org> --limit 1000`, because a token without access also gets 404. Then remove the record and add its id to `_graveyard.md`. Remove at most 10 per run |
| `manual` | A Confluence or Slack record. Check it with a connector or by hand, then `--apply --confirm FILE` with `{record_id: evidence}` |
| `error` | Skip it. Note it in the pull request body |

Exit code 2 means the run itself failed.

Also refresh the repo catalog: `python3 scripts/repo_catalog.py --today <date>`.

## Fleet health

`fleet-health.yml` runs every Monday at 06:00 UTC. It reads the telemetry comments and prints a report in the run summary. A red run means an alert fired. Run it by hand with `gh workflow run fleet-health.yml`. Run `python3 scripts/fleet_health.py --days 30 --repo <owner>/<repo>` for a local copy.

The report undercounts. A user without a logged-in `gh` sends nothing.

| Alert | First action |
|---|---|
| No sessions in the window | Check the plugin release and the `telemetry` issues. Ask a teammate to run a session |
| `ranker_install` failed | Ask the user for `install.log` in the plugin data folder. Fix the installer |
| Swallowed plugin errors | Find the `<place>:<ExcType>` key in the plugin code. Write a defect file if you do not fix it |
| Selector p95 above 8000 ms | Check `ranker_install` per user. Check the Haiku timeout |

To stop the alert mail, disable the workflow in the Actions tab.

Read the telemetry for gaps too. `misses` lists searches with no result, which are missing entries. `hits` shows entries that nobody reads.

## Defects

Write each defect you find and do not fix to its own file in `defects/`. One file per defect means parallel pull requests never conflict. The pull request that fixes a defect deletes its file. See `defects/README.md`.

## Evals and baseline

`evals/select_cases.jsonl` holds prompts and the vault ids the selector should pick. `evals/floor.json` holds the minimum quality. `evals/baseline.json` holds the last measured result and the hash of the selector code.

After you replace the stub vault, replace the cases:

1. Write 20 or more cases that name your real hub ids. Include a few prompts that need no vault entry (negative cases). Keep about 20% as a holdout.
2. Run the bench. It needs `CLAUDE_PLUGIN_DATA` with the local runtime installed, and a working `claude`:

   ```bash
   python3 scripts/bench_hook.py --json evals/baseline.json --floor evals/floor.json
   ```

3. Commit the new `baseline.json`. The test `test_baseline_matches_selector_code` fails when the selector code changed and the baseline did not.

The template baseline holds placeholder numbers that satisfy the floor. They are not a measurement. Replace them.

## Releases

A merge to `main` is the release. Every user gets it about one hour after the next session start. The new version becomes active on the launch after that. Rollback is a revert pull request. After a change to plugin code or `knowledge.json`, run `python3 scripts/build_integrity.py` in the same pull request. CI checks it.

## Local gate before a push

```bash
pytest -q tests
ruff check .
python3 scripts/check_knowledge_graph.py
python3 scripts/build_integrity.py --check
```

Run the full gate for any change that touches a non-docs file. A docs-only change needs none of it.

See also: [../maintainer-runbook.md](../maintainer-runbook.md) for the day-to-day steps, and `knowledge/` for the rules about GitHub Actions and Claude Code.
