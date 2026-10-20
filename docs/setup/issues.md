# Step 4: Issues

The plugin talks to your team through GitHub issues in one repo: `issues_repo` in `plugins/knowledge/knowledge.json`. This step sets up that repo, its labels and its access. Without it, capture, proposals, the offline queue and telemetry stay off.

Contents: [The issues repo](#the-issues-repo), [Labels](#labels), [The four flows](#the-four-flows), [Access](#access), [Opt-outs](#opt-outs), [Test it](#test-it).

## The issues repo

Use your private repo from step 1. You can use another private repo when you want issues apart from code. Set the value with `adapt.py --issues-repo <owner>/<name>`, or edit `knowledge.json`.

Rules:

- The repo must be private. Issues hold raw work notes, vault ids and search terms. In a public repo they are public.
- Issues must be on. Check the repo settings, Features, Issues.
- Rebuild `integrity.json` after you edit `knowledge.json` (`python3 scripts/build_integrity.py`).

## Labels

`gh issue create --label <name>` fails when the label does not exist. Create the labels once. Replace `<owner>/<repo>` with `issues_repo`.

```bash
R=<owner>/<repo>
gh label create capture            -R $R --color 1D76DB --description "Raw work knowledge filed by the plugin"
gh label create knowledge-proposal -R $R --color 0E8A16 --description "A targeted vault change"
gh label create needs-check        -R $R --color FBCA04 --description "Evidence is missing or disputed"
gh label create telemetry          -R $R --color 5319E7 --description "Plugin usage counts, one issue per user"
```

Use `--force` to update a label that exists. The PR review labels (`claude: approved`, `claude: changes needed`, `knowledge: approved`, `knowledge: changes needed`) are created by the review workflows. See [ci.md](ci.md).

| Label | Set by | Used by |
|---|---|---|
| `capture` | The capture worker and Claude (`--label capture`) | The distil routine |
| `knowledge-proposal` | The issue form and Claude (`knowledge-propose` skill) | The distil routine |
| `needs-check` | The issue form when evidence is unchecked, or a reviewer | The distil routine verifies these harder |
| `telemetry` | The plugin, on the first session of a user | `scripts/fleet_health.py` and the distil routine |

## The four flows

**Capture.** A `Stop` hook starts a background worker when the new transcript text reaches 30000 bytes. `SessionEnd` and `PreCompact` start it for 2000 bytes or more. The worker asks Claude Haiku for work knowledge that fits `scope`. It files issues titled `capture: <summary>` with the label `capture`. The capture gate drops secrets and emails. The worker runs with no prompt. Each run adds a line to `capture/worker.log`.

**Knowledge proposal.** When Claude learns a fact in a session, the `knowledge-propose` skill files an issue titled `knowledge: <summary>` with the label `knowledge-proposal`. The body has five headings: Change type, Target, Proposed text, Evidence and Source project. A person can use the issue form: New issue, then Knowledge proposal. The form is `.github/ISSUE_TEMPLATE/knowledge-proposal.yml`. The format is in [../cron-contract.md](../cron-contract.md).

**Offline queue.** If `gh` fails (no network, no login), the issue goes to the `queue` folder in the plugin data folder. The hook files it at the next session start or end. Files stay on disk when `issues_repo` is empty, so nothing is lost if you set it later.

**Telemetry.** At session end, and before each context compact, the plugin adds one comment to the issue `telemetry: <github login>`. The comment holds one fenced JSON block of counts: vault files read, searches with no result, selector picks and misses, timings and error counts. It never holds prompts or code. The field list is in [../cron-contract.md](../cron-contract.md). The workflow `fleet-health.yml` reads these comments every Monday and prints a report.

## Access

Every teammate needs access to the issues repo.

- Give the team the Write role on the issues repo. GitHub sets labels on a new issue only for users with push access, and drops them silently for others (REST API, "Create an issue"). Without labels, the plugin cannot find its `telemetry` issue, and the maintainer cannot find `capture` issues.
- Teammates need a logged-in `gh`. The plugin uses `gh issue create`, `gh issue list` and `gh issue comment`.
- The plugin sets `GH_REPO` for the capture worker child, so it never files to the repo of the current folder.

## Opt-outs

| Goal | How |
|---|---|
| Turn off all filing for everyone | Set `issues_repo` to `""`, rebuild `integrity.json`, publish |
| Turn off telemetry for one user | `export KNOWLEDGE_TELEMETRY=0` |
| Turn off capture for one user | `export KNOWLEDGE_CAPTURE=0` |
| Turn off the weekly report | Disable the `Fleet health` workflow in the Actions tab |
| Run the capture worker without filing | `export KNOWLEDGE_DRY_RUN=1` |

Setting `KNOWLEDGE_TELEMETRY=0` does not stop proposals. Claude can still file a proposal when you ask it. Telemetry off also stops the stale-session sweeps.

Put the opt-out variables in your onboarding doc. Tell the team what the plugin sends. The text is in the Privacy section of the root [README](../../README.md).

## Test it

1. Set `issues_repo` and install the plugin on your machine.
2. Run a short session and quit. A `telemetry: <login>` issue appears when the session had vault activity.
3. Ask Claude: "File a knowledge proposal: test item, change type add, target new: example/knowledge.md." Check that the issue has the label `knowledge-proposal`.
4. Close the test issues.
5. Run `python3 scripts/fleet_health.py --days 7 --repo <owner>/<repo>`. It prints a report.
