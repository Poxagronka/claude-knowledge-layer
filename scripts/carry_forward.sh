#!/bin/bash
# Carry the last bot approval forward when the reviewed code did not change.
# Usage: carry_forward.sh code|vault
# Env: GH_TOKEN REPO PR BASE_REF HEAD_SHA GITHUB_OUTPUT
# The hash of the diff (merge-base..commit, binary, no index/hunk lines) is the
# same for the approved commit and the new head, so a rebase or a docs-only push
# needs no new review. Env CARRY_REMOTE overrides the fetch URL (tests).
# Runs under pull_request_target: it only fetches refs and diffs them. It never
# checks out or runs PR code. Any failure means "not carried": a full review runs.
set -uo pipefail
case "${1:-}" in
  code) paths=(. ':(top,glob,exclude)*.md' ':!docs' ':!knowledge' ':!defects' ':!plugins/knowledge/vault' ':!plugins/knowledge/integrity.json') ;;
  vault) paths=(plugins/knowledge/vault) ;;
  *) echo "usage: carry_forward.sh code|vault"; exit 0 ;;
esac
out="${GITHUB_OUTPUT:-/dev/null}"
echo "carried=false" >> "$out"

old="$(gh api "repos/$REPO/pulls/$PR/reviews" --paginate \
  -q '.[] | select(.user.login == "github-actions[bot]" and (.state == "APPROVED")) | .commit_id' \
  | tail -n 1)"
[ -n "$old" ] || { echo "no earlier bot approval: full review"; exit 0; }
[ "$old" != "$HEAD_SHA" ] || { echo "approval is for this head already: full review"; exit 0; }

url="${CARRY_REMOTE:-https://github.com/${REPO}.git}"
auth="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
gfetch() { git -c "http.https://github.com/.extraheader=$auth" fetch -q --no-tags "$@"; }
refs=("+refs/heads/$BASE_REF:refs/carry/base" "+refs/pull/$PR/head:refs/carry/new")
gfetch --unshallow "$url" "${refs[@]}" 2>/dev/null \
  || gfetch "$url" "${refs[@]}" \
  || { echo "fetch failed: full review"; exit 0; }
gfetch "$url" "+$old:refs/carry/old" \
  || { echo "commit $old is gone (force push?): full review"; exit 0; }

diff_hash() {
  local mb
  mb="$(git merge-base refs/carry/base "$1")" || return 1
  git diff --binary "$mb" "$1" -- "${paths[@]}" | grep -v -e '^index ' -e '^@@' | sha256sum | cut -d' ' -f1
}
a="$(diff_hash "$old")" || a=""
b="$(diff_hash "$HEAD_SHA")" || b=""
echo "diff hash old=$a new=$b"
empty="$(printf '' | sha256sum | cut -d' ' -f1)"
if [ -n "$a" ] && [ "$a" = "$b" ] && [ "$a" != "$empty" ]; then
  echo "same $1 diff as approved commit $old: carrying the review forward"
  mkdir -p .review-ctx
  jq -nc --arg s "$HEAD_SHA" '{verdict:"approved",head_sha:$s}' > .review-ctx/verdict.json
  echo "carried=true" >> "$out"
fi
exit 0
