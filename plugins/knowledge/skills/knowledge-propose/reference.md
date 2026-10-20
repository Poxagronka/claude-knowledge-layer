# knowledge-propose: reference

`<issues repo>` stands for the `Issues repo` value in the session header (the `issues_repo` key of `knowledge.json`). Replace it in every command below.

## Fields

- Change type: `add`, `fix`, `remove`, `lesson` or `tool`.
- `lesson`: a lesson you derived yourself that is useful to everyone. Target is the matching `lessons/<domain>.md` file. The entry uses the lesson format: block id `^L-<slug>`, meta fields `added`, `source` (`issue/<n>`), `origin:: agent` and `status`. Link at least one hub.
- `tool`: a reusable tool useful to the organization (a script such as a translation helper, a Claude skill, a bot, a CLI, a notebook). Target is `new: entities/tool-<slug>.md`, like `tool-example-tool`. The text gives what it does, where it lives (repo/path or URL), the author (name only), how to run it in one line, and the related hubs. Evidence is the file or repo path you checked this session, when you have it. Skip personal one-off scripts and generic tools with no use for the organization.
- Slack evidence: a Slack message you read this session counts as live evidence. Cite it as `slack:<channel id>:<ts>`. Never quote personal data from it.
- Target: `domain/file.md#^id` for an existing entry. Use `new: domain/file.md` for a new entry.
- Proposed text: the entry in the vault format. See `vault/_template.md`. Write the heading or bullet and the meta line. The meta line needs `verify_with`, and `verify_query` unless it is `none`.
- Evidence: optional. The command and its output, a URL, or `file:line`. Without it, file with `needs-check`. For a `fix` or `remove` from a failed verify check, give the `verify_query` and its output.
- Source project: the repo or project where you found it. Optional.

Never propose emails, phones or other contacts. People appear by name and role only.

File any work knowledge. The maintainer cron applies the relevance filter. Skip what is not work: study questions, private pet projects, generic programming Q&A with no work context. Notes about this repo itself go to its root `knowledge/` folder through a PR, not an issue.

## Labels

- `capture`: raw work knowledge. The target may be `none` or a suggested hub. Change type is `add`, `lesson` or `tool`. Title starts with `capture: `.
- `knowledge-proposal`: a fix, removal or edit of a named vault target. Title starts with `knowledge: `.

Capture issues are raw material, visible to everyone who can read the issues repo. The cron closes them after processing. The privacy filter applies in full.

## Issue body

Use exactly these headings, in this order. The maintainer job parses them.

```markdown
### Change type

fix

### Target

example/rules.md#^R1

### Proposed text

<the entry text>

### Evidence

<command and output, URL, or file:line. Write `none` when you have no evidence>

### Source project

<optional>
```

Hard format rules:

- The title starts with `capture: ` or `knowledge: `.
- The body uses exactly the five headings above, in that order.
- One issue holds one item. Do not write `## Item` for a single item.
- Do not use an H2 inside Proposed text.
- Target is the bare hub id (for example `vendor-example-vendor`), or `path#^id`, or `new: path`. Add no annotations.
- A meta line uses the `[key:: value]` syntax. `verify_with` is only `github`, `directory`, `confluence`, `slack` or `none`. For a web page, write `verify_with:: none` and `source:: url:<URL>`.
- Evidence cites URLs and dates only. Never write "this session" or a tool-permission note.

## Privacy filter

Run it first, before anything else. Drop the item silently and show nothing when it holds:

- secrets, tokens, emails, phones or other contacts;
- personal data beyond name and role;
- a sensitive topic: money, HR, partners' confidential terms;
- personal non-work content: private life, health, family, opinions about people.

Never file it, never quote it, never mention it.

## Batching

Several small finds from one task and one label go into ONE issue. Repeat the five headings once per item under a numbered section `## Item 1`, `## Item 2`. Keep the headings in the same order inside each section. A single item uses the plain format above. Dedup runs per target before you build the issue.

```markdown
## Item 1

### Change type

add

### Target

vendor-example-vendor

### Proposed text

<entry text>

### Evidence

<URL and date>

### Source project

<optional>

## Item 2

### Change type

fix

### Target

example/rules.md#^R1

### Proposed text

<entry text>

### Evidence

<URL and date>

### Source project

<optional>
```

## Dedup and file commands

Every `gh issue` command targets the issues repo with `-R <issues repo>`, because the plugin runs from other repos.

```bash
R=<issues repo>
gh issue list -R $R --label knowledge-proposal --state open --search "<target>"
gh issue list -R $R --label capture --state open --search "<subject>"
gh issue comment <n> -R $R --body "<new evidence>"
gh issue edit <n> -R $R --add-label needs-check
gh issue create -R $R --label knowledge-proposal --title "knowledge: <short summary>" --body-file <tmp>
gh issue create -R $R --label capture --title "capture: <short summary>" --body-file <tmp>
```

Same target or subject open under either label: comment instead of create. DECIDE and the privacy filter already ran, so a comment never carries a sensitive or declined item. A disputed addition: the comment text starts with `needs-check:` and you run the `gh issue edit` line on that issue. Write the body to a temp file first. A comment that proposes a `fix` or `remove` of an existing record starts its body with the same two fields as the issue form, so telemetry can count it:

```
### Change type
fix

### Target
entities/metric-example-metric
```

## DECIDE outcomes

- a. File normally: live evidence, or the user stated the fact and agreed to record it.
- b. No evidence or disputed: add `--label needs-check` to `gh issue create` and say so in the body.

Sensitive, personal or declined items never reach DECIDE. The privacy filter and the user's refusal drop them silently.

## Report

Print only the issue URL you filed or commented on, or `NONE`. Print nothing else. The worker logs the URL in `worker.log`.

## Offline queue

`gh` may be missing, logged out or offline. Then `gh issue create` fails. Do not lose the finding. Write the issue as one JSON file into the queue folder. Its absolute path is the `Queue path:` line of the session-start header, or the path in your prompt. Use a unique name such as `<unix time>-<label>.json`. Write the whole file in ONE call (the worker has only the Edit tool for this folder: create the file with an empty `old_string`). Do not build it in many steps. You cannot move files, so the flush protects itself: it skips a file younger than 5 seconds and keeps a file with broken JSON for 60 seconds before it drops it.

The background worker has no file tools and no `gh issue create` or `gh issue comment`. For each new capture it prints `QUEUE: <compact JSON>` to stdout, and the hook writes the queue files itself. For a duplicate of an open issue it prints nothing (no comment on duplicates).

```json
{"title": "capture: <short summary>", "body": "<full issue body>", "labels": ["capture"]}
```

`labels` is a list with every label of the call (a single string is accepted), for example `["knowledge-proposal", "needs-check"]`. The plugin hook flushes the queue at session start and session end. It runs `gh issue create -R <issues repo>` for each file in order and deletes the file when gh succeeds. A file that fails stays queued, and the flush goes on to the next file unless `gh` is missing or logged out. A `.sending` file older than 10 minutes goes back to the queue. A file older than 7 days is dropped. Dedup does not run on flush, so a queued item may repeat an issue that the cron already knows.

## Background capture worker

A `Stop` hook prints nothing and never blocks. It starts a detached `hook.py capture-worker` only when the new transcript text since the last offset is 30000 bytes or more (run reason `volume`). The `SessionEnd` and `PreCompact` hooks flush the remaining text the same way when it is 2000 bytes or more (reasons `session_end` and `precompact`). The worker reads ALL the new part of the session transcript (user text, assistant text and tool results). It splits text over 60 KB into chunks and runs the child once per chunk. The byte offset lives in `${CLAUDE_PLUGIN_DATA}/capture/<sid>.offset`. It runs `claude -p --model haiku` from a neutral folder with `KNOWLEDGE_WORKER=1` and `GH_REPO=<issues repo>`. That child follows this skill up to the dedup step, then prints one `QUEUE:` line per new capture, or `NONE`. It never creates issues. The worker writes the queue files and calls the queue flush at once (no 5 s settle wait), so the issues are filed in the same run. The offset moves only when the child exits 0. A per-session lock (`<sid>.lock`, stale after 10 minutes) stops two workers on one session. Every hook no-ops when `KNOWLEDGE_WORKER=1`.

Each run adds one line to `${CLAUDE_PLUGIN_DATA}/capture/worker.log`: time, session, run reason (`volume`, `session_end` or `precompact`), bytes read, duration in seconds, exit code, issue URL, `queued=N filed=M`. The log trims at 1 MB. Set `KNOWLEDGE_DRY_RUN=1` to make the child print `WOULD FILE` and its `QUEUE:` lines. The worker then neither queues nor flushes. Opt out: `KNOWLEDGE_CAPTURE=0`.
