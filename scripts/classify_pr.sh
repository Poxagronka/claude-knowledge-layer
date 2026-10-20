#!/usr/bin/env bash
# Classify a PR as docs-only and/or touching the knowledge vault.
#
# CI use:   REPO=... PR=... GH_TOKEN=... scripts/classify_pr.sh
#           appends `docs_only=` and `has_knowledge=` to $GITHUB_OUTPUT.
# Test use: scripts/classify_pr.sh --paths-file <file>
#           reads one path per line and prints the two lines to stdout.
#
# Workflows run this script from the BASE commit, never the PR head. A PR
# could otherwise rewrite its own classifier.
set -euo pipefail

classify() {
  local file="$1"
  # Fail CLOSED on an empty list: `grep -qv` on empty input returns 1, which
  # would read as docs-only and hand out an approval with nothing reviewed.
  if [ ! -s "$file" ]; then
    echo "docs_only=false"
    echo "has_knowledge=true"
    return
  fi
  # Any path in the vault sends the PR through the knowledge review.
  if grep -qE '^plugins/knowledge/vault/' "$file"; then
    echo "has_knowledge=true"
  else
    echo "has_knowledge=false"
  fi
  # A path is docs when it is markdown or under docs/ or knowledge/, except
  # under plugins/: skills and agents there are instructions that run in
  # many sessions, so they count as code. Only the vault is docs there.
  local docs_only=true path
  while IFS= read -r path; do
    case "$path" in
      # The verify log is written by the script. Only this narrow path counts as docs.
      plugins/knowledge/vault/_verify/*.json) continue ;;
      plugins/knowledge/vault/*) ;;
      plugins/*) docs_only=false ;;
    esac
    if ! printf '%s\n' "$path" | grep -qE '(^(docs|knowledge)/|\.md$)'; then
      docs_only=false
    fi
  done < "$file"
  echo "docs_only=$docs_only"
}

if [ "${1:-}" = "--paths-file" ]; then
  classify "$2"
  exit 0
fi

paths="$(mktemp)"
# NOT `gh pr view --json files`: that GraphQL field caps at 100 files, so a PR
# whose 101st path is code would classify as docs-only. REST names the field
# `filename`. The per-element `-q` is required: `--paginate` applies the filter
# per page. `previous_filename` covers a rename out of the vault or out of code.
gh api "repos/$REPO/pulls/$PR/files" --paginate \
  -q '.[] | .filename, (.previous_filename // empty)' > "$paths"
echo "changed paths:"; cat "$paths"
classify "$paths" | tee -a "${GITHUB_OUTPUT:-/dev/null}"
