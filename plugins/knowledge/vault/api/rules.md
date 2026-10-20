---
id: api/rules
type: rule
tags: [domain/api]
---
# Internal API rules

Rules for access and use of your internal APIs. See the hub [[service-example-service]].

## R1 — Use one personal API key per user
[added:: 2026-01-15] [source:: issue/4] [verify_with:: none] [status:: active] ^R1
Each person asks for a personal key. A shared key hides who made a call. Keep the key in an env variable. Never put a key in chat, a ticket, a doc, a commit or this vault. See [[service-example-service]].
