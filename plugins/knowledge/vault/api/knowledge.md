---
id: api/knowledge
type: fact
tags: [domain/api]
---
# Internal API knowledge

Facts about your internal APIs: base URLs by name, endpoints, limits and quirks. Never write secret values. See [[service-example-service]] and [[datasource-example-datasource]].

## Facts

- 2026-01-15: **the orders endpoint pages with a cursor, not an offset**. Pass the `next` value from the last response. See [[service-example-service]].
  [added:: 2026-01-15] [source:: issue/5] [verify_with:: none] ^K-api-orders-cursor
