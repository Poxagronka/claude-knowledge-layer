---
id: metric-example-metric
type: entity
tags: [kind/metric]
aliases: [Weekly Active Shops, WAS]
verify_with: none
---
# Weekly Active Shops (sample metric)

Only add a metric that your organization defines in its own way. No textbook definitions.

Definition: a shop is active when it sends at least one order in the week. The count uses UTC weeks. Test shops are out.

Threshold: a drop of more than 10% week over week opens an incident review. This is a lasting rule. The live value stays in [[datasource-example-datasource]].

Related: [[product-example-app]], [[process-example-process]], [[datasource-example-datasource]]
