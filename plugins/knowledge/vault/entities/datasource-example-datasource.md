---
id: datasource-example-datasource
type: entity
tags: [kind/datasource]
aliases: [Example warehouse, analytics database]
verify_with: none
---
# Example warehouse (sample data source)

Say what lies here, how to reach it and which questions it answers. Never write secret values or live numbers.

Access: SQL client with the env vars `WAREHOUSE_URL` and `WAREHOUSE_TOKEN`. Names only.

Index:
- `orders`: one row per order.
- `shops`: one row per shop.

Answers:
- How many shops sent an order this week? See [[metric-example-metric]].
- Which service writes the `orders` table? See [[service-example-service]].

Related: [[service-example-service]], [[metric-example-metric]], [[vendor-example-vendor]]
