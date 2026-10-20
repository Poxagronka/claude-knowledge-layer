#!/bin/bash
# Print the PR diff as one unified diff, built from the paginated REST files
# list. `gh pr diff` fails with HTTP 406 above 300 files.
# Env: GH_TOKEN, REPO, PR. A file with no patch (binary or too large) gets a
# header and a "Binary files differ" line so the slice still exists.
set -euo pipefail
repo="${REPO:-${GITHUB_REPOSITORY:-}}"

gh api --paginate "repos/$repo/pulls/$PR/files" | jq -r '
  .[] |
  (.previous_filename // .filename) as $old |
  "diff --git a/\($old) b/\(.filename)",
  (if .status == "added" then "--- /dev/null" else "--- a/\($old)" end),
  (if .status == "removed" then "+++ /dev/null" else "+++ b/\(.filename)" end),
  (if (.patch // "") == "" then "Binary files differ" else .patch end)'
