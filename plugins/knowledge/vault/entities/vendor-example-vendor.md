---
id: vendor-example-vendor
type: entity
tags: [kind/vendor]
aliases: [Example Vendor, EV]
verify_with: none
---
# Example Vendor

<What the vendor supplies and how your organization uses it.> Example Vendor sends transactional email for [[product-example-app]].

- Access: one API key in the env var `EXAMPLE_VENDOR_API_KEY`. Never print the value.
- Quirk: the sandbox accepts any address. Production rejects unverified senders.
- Contract and prices stay out of the vault.

Related: [[service-example-service]], [[datasource-example-datasource]], [[platform-example-platform]]
