---
id: example/rules
type: rule
tags: [domain/example]
---
# Example domain rules

A rule is an instruction that holds until it is changed. Use one file `<domain>/rules.md` per topic. Rules have the block id `^R<n>`. See [[org-example]].

## R1 — Pin the base image of every service
[added:: 2026-01-15] [source:: issue/1] [verify_with:: none] [status:: active] ^R1
Pin the container base image to a digest. A floating tag changed behavior once without a code change. See [[service-example-service]].

