---
id: github/knowledge
type: fact
tags: [domain/github]
---
# GitHub facts

## Org and branch settings

- 2026-10-17: **In a GitHub organization, an enforced security configuration can block enabling secret scanning per repo.** The API returns HTTP 422 "An enforced security configuration prevented modifying secret scanning enablement". Only an org owner can change it. Ask an owner instead of retrying.
  [added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] ^K-secret-scanning-enforced
- 2026-10-17: **Adding required status checks to existing branch protection needs a full `PUT .../branches/main/protection`.** `PATCH .../required_status_checks` returns 404 "Required status checks not enabled" when none exist yet.
  [added:: 2026-10-17] [source:: template] [verify_with:: none] [last_verified:: 2026-10-17] ^K-required-checks-put
- 2026-10-18: **A gitleaks job that reads `.github/config/gitleaks.toml` from the PR head lets a PR change its own scan rules.** Review any change to this config. A false positive needs the inline marker `gitleaks:allow`, not a global allowlist regex.
  [added:: 2026-10-18] [source:: template] [verify_with:: none] [last_verified:: 2026-10-18] ^K-gitleaks-config-from-pr-head
