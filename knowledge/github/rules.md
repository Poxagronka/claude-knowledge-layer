---
id: github/rules
type: rule
tags: [domain/github]
---
# GitHub Actions rules

## R1 — Pass GITHUB_TOKEN to claude-code-action under pull_request_target
With its default GitHub App auth, `anthropics/claude-code-action` fails under `pull_request_target` with `Invalid OIDC token`. Pass `github_token: ${{ secrets.GITHUB_TOKEN }}`.
[added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] [status:: active] ^R1

## R2 — Approve in the same job that sets the verdict label
Labels, comments and other events made with `GITHUB_TOKEN` do not trigger other workflows. A job that sets a verdict label must approve in the same job. Do not rely on a `labeled` workflow.
[added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] [status:: active] ^R2

## R3 — Run review and approval on pull_request_target
With `on: pull_request`, GitHub runs the workflow file from the PR branch. A PR can rewrite its own review or approval logic.
Run review and approval on `pull_request_target`. It uses the workflow from the base branch. Check out the PR head as data only and never execute it. Keep test jobs that execute PR code on `pull_request`.
[added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] [status:: active] ^R3

## R4 — Approve a workflow-switch PR by hand once
A PR that switches workflows from `pull_request` to `pull_request_target` runs neither version. It needs one manual approval.
[added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] [status:: active] ^R4

## R5 — Do not set track_progress on claude-code-action under pull_request_target
`track_progress: true` forces tag mode. Tag mode fetches the PR head and checks it out into the workspace root. With `persist-credentials: false` the fetch fails with `could not read Username`. Had it worked, the root would hold PR code. Give a `prompt` and no `track_progress`, so the action runs in agent mode. Agent mode does not check out the PR.
[added:: 2026-10-17] [source:: template] [verify_with:: github] [verify_query:: github:path:anthropics/claude-code-action:src/modes/detector.ts] [last_verified:: 2026-10-17] [status:: active] ^R5

## R6 — A missing verdict must fail the job
A headless `claude-code-action` run ends when the model ends its turn. Background subagents are lost then. The model said "passes are still running, holding here", no `verdict.json` was written, and the job still passed, so the PR hung. Run subagent passes in the foreground and write the verdict before the final message. A missing verdict, or one for another SHA, must fail the job.
[added:: 2026-10-17] [source:: template] [verify_with:: docs] [last_verified:: 2026-10-17] [status:: active] ^R6
