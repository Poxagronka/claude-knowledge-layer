#!/bin/bash
# Trusted step of the review workflow: turn .review-ctx/verdict.json into
# labels and, when every condition holds, one pinned bot approval.
# Usage: apply_verdict.sh code|knowledge
# Env: GH_TOKEN, PR, REPO, AUTHOR_OK, and HAS_KNOWLEDGE (code) or DOCS_ONLY (knowledge).
# It fails closed: a missing verdict or a verdict for another SHA exits 1.
set -euo pipefail

mode="${1:?usage: apply_verdict.sh code|knowledge}"
case "$mode" in
  code)      mine=claude;    other=knowledge
             [ "${HAS_KNOWLEDGE:-}" != false ] && other_needed=true || other_needed=false ;;
  knowledge) mine=knowledge; other=claude
             [ "${DOCS_ONLY:-}" != true ] && other_needed=true || other_needed=false ;;
  *) echo "unknown mode: $mode" >&2; exit 2 ;;
esac

verdict=changes_needed; sha=""
if [ -f .review-ctx/verdict.json ]; then
  verdict="$(jq -r '.verdict // ""' .review-ctx/verdict.json 2>/dev/null || true)"
  sha="$(jq -r '.head_sha // ""' .review-ctx/verdict.json 2>/dev/null || true)"
fi
[ "$verdict" = approved ] || verdict=changes_needed
if [ "$mode" = knowledge ]; then
  # The gate must exit 0 on this head, whatever the agent said.
  [ "$(cat .review-ctx/gate.rc 2>/dev/null || echo 1)" = 0 ] || verdict=changes_needed
fi

current="$(gh pr view "$PR" -R "$REPO" --json headRefOid -q .headRefOid)"
if [ "$sha" != "$current" ]; then
  # A silent success would leave the PR with no label and no approval.
  echo "::error::verdict is for '$sha', head is '$current': no verdict for this head (the review ended early or an earlier step failed)"
  exit 1
fi

gh pr edit "$PR" -R "$REPO" --remove-label "$mine: approved" \
  --remove-label "$mine: changes needed" 2>/dev/null || true
if [ "$verdict" != approved ]; then
  gh pr edit "$PR" -R "$REPO" --add-label "$mine: changes needed"
  exit 0
fi
gh pr edit "$PR" -R "$REPO" --add-label "$mine: approved"
[ "${AUTHOR_OK:-}" = true ] || exit 0

labels="$(gh pr view "$PR" -R "$REPO" --json labels -q '.labels[].name')"
! grep -qxF "$mine: changes needed" <<< "$labels" || exit 0
! grep -qxF "$other: changes needed" <<< "$labels" || exit 0
if [ "$other_needed" = true ]; then
  grep -qxF "$other: approved" <<< "$labels" || exit 0
fi

# Pinned to the reviewed commit. Skip when this commit is approved already.
if gh api "repos/$REPO/pulls/$PR/reviews" --paginate \
    -q '.[] | select(.state == "APPROVED" and .user.login == "github-actions[bot]") | .commit_id' \
    | grep -qxF "$sha"; then
  echo "commit $sha is approved already"
  exit 0
fi
gh api -X POST "repos/$REPO/pulls/$PR/reviews" -f event=APPROVE -f commit_id="$sha" >/dev/null
