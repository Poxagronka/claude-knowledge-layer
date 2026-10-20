---
id: person-jane-doe
type: entity
tags: [kind/person]
aliases: [Jane Doe]
title: Engineering Manager
department: Platform
github: jane-doe-example
verify_with: none
---
# Jane Doe

Engineering Manager in the Platform department. Owns [[service-example-service]].

Team: [[team-example]]. Frontmatter holds the role only. Never add an email, a phone or other contacts.

To check a person against your directory, set `verify_with: directory` and `verify_query: directory:user:<github login>`.

Related: [[team-example]], [[org-example]]
