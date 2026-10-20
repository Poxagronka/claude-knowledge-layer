---
name: knowledge-propose
description: "Use when you learn any work fact about the organization named in the session header: teams, products, services, vendors, competitors and market, platform policy, metrics, processes, people and roles, internal tools or AI. Examples: a vendor policy change, a competitor fact, a metric definition, an owner, a decision. Files a capture or knowledge-proposal GitHub issue. Not for code details."
---

# knowledge-propose

Every filing is a GitHub issue. A maintainer job checks it and edits the vault. The issues repo is the `Issues repo` named in the session header. When the header says issue filing is off, stop and show nothing. `<issues repo>` below stands for that value. Never edit the vault directly.

Two labels:

- `capture`: raw work knowledge of any kind. The maintainer job decides if it is relevant to the organization.
- `knowledge-proposal`: a targeted fix, removal or edit of a named vault entry.

The bar at intake is low. The bar at the cron is high. File any work knowledge that is even slightly useful. The cron filters for relevance to the organization, distils it, checks the facts and writes to the vault only what passes.

Details: [reference.md](reference.md).

| # | STATE | exit criterion (observable evidence) | on fail |
|---|---|---|---|
| 1 | FILTER | Every item passed the privacy filter: no secrets, no personal data beyond name and role, no sensitive topic (money, HR, partners' confidential terms), no personal non-work content | Item fails: drop it silently and show nothing. No items left: stop |
| 2 | COLLECT | For each item you have the label (`capture` or `knowledge-proposal`), the change type, the target and the proposed text. A `capture` target may be `none` or a suggested hub. Evidence is optional | Item not about work (study question, private pet project, generic programming Q&A with no work context): skip it |
| 3 | DECIDE | One DECIDE outcome below applies to each item. The user did not say on their own to skip it (nobody asks them) | The user said to skip it: drop it and show nothing |
| 4 | DEDUP | `gh issue list -R <issues repo> --label <label> --state open --search "<target>"` ran for each item, for both labels. Same target or same subject found: add a comment (see reference.md) and go to REPORT with that URL. None found: go on | `gh` fails: queue the issue (see reference.md, Offline queue) and say nothing |
| 5 | FILE | All new items of one label are in ONE `gh issue create -R <issues repo> --label <label>` call (`## Item <n>` section per item) and it printed an issue URL. A `capture` and a `knowledge-proposal` item make two calls. Outcome b adds `--label needs-check` | `gh` fails: queue the issue (see reference.md, Offline queue) and say nothing |
| 6 | REPORT | Print the issue URL, or `NONE`. In the background worker this is the only output. In a main session, add no line to the answer | none |

Change types: `add`, `fix`, `remove`, `lesson` and `tool`. A lesson that an agent derived and that is useful to everyone targets the matching `lessons/<domain>.md` with origin `agent` and status `hypothesis`.

Rules for the proposal:

- Scope: the `scope` key of `knowledge.json` (teams, products, services, vendors, competitors, market, platform policy, metrics, processes, people and roles, internal tools, AI). Code details are out of scope.
- Work-related and slightly useful is enough: facts, owners, decisions, rules, lessons, tools, where-to-find-X. Do not judge relevance. The cron does.
- A fix, removal or edit of an existing vault target uses `knowledge-proposal`. Everything else uses `capture`.
- Out of scope means not about work. Skip it.
- DECIDE has two outcomes. It never asks the user.
  - a. Live evidence: file normally. The evidence is a source checked this session (command output, `file:line`, API response, URL, or a Slack message you read cited as `slack:<channel id>:<ts>`), or the user stated the fact and agreed to record it. For `remove` the source shows it is gone.
  - b. No evidence or disputed: file with `--label needs-check`. The claim rests on memory or the user's words alone, or sources contradict. Say so in the body. The maintainer decides.
- Several small items from one task go into one issue. Dedup still applies per target.
- Why: an issue is a proposal. The maintainer cron verifies it before any vault edit.

UX rule: a background worker runs this skill on batches of new transcript text and nothing shows in the chat. The main model files captures only when the user asks. Never mention a proposal in an answer. Never ask the user to run commands.
