---
id: _template
type: map
tags: [meta/template]
---
# The knowledge vault format

This file is the format contract for the vault. The vault is an Obsidian-style graph.
`scripts/check_knowledge_graph.py` is the gate. When this file and the gate disagree, the gate is the truth.
The hub kinds and their size caps come from `plugins/knowledge/knowledge.json` (`hub_kinds`). This file shows the default kinds.

## What belongs here

The vault holds knowledge of <your organization>: <teams, products, services, metrics, vendors, processes, tools, lessons and decisions>.

Owner rules (edit this list to fit your organization, keep the numbers stable):

1. Only <organization>-specific knowledge. No generic tool facts (<for example Git, Python, public definitions>). Maintainer notes about this repo go to the root `knowledge/` folder.
2. Numbers and money are allowed when the entry states "as of <date>" and the source. Live performance and finance metrics that have a live source are banned: <revenue, profit, KPI values, spend>. Link to the live source instead: <dashboard, BI tool, planning page>. A threshold stated as a lasting rule is allowed, for example <a launch gate at a fixed percentage>.
3. The most valuable kinds: lessons learned, definitions, processes, structure (teams, systems, products, repos), decisions with reasons, and "where to find X".
4. People appear by name and role only. Never emails, phones or other contacts. A person has a hub in the People category.
5. Every entry links at least one hub. The gate fails on an orphan.
6. Reference digests: a distilled digest of external documentation is allowed when the doc covers a vendor, platform or API that <your organization> uses and that has a hub. One entry holds one fact that the organization applies. The entry names the primary URL and "as of <date>". A reader re-checks the page when the date is older than about 90 days.
7. Reference cards for internal docs: for a Confluence page or other internal doc, the vault keeps a reference card. The card holds the link, its freshness, the index of sections and a short summary. It is not a copy of the content. A card is only for a durable reference that people reuse: a playbook, a standing process, a register or a method. One-off pages get no card.

An entry must be useful. That is, it changes a decision or action of a reader in [[org-example]].

## Categories

| Category | Path | Holds |
|---|---|---|
| Hubs | `entities/` | one note per node: the organization, teams, products, services, metrics, vendors, processes, initiatives, tools |
| People | `entities/person-<slug>.md` | one hub per member: name, role and department only |
| Domains | `<domain>/` | rules, facts and hypotheses of one topic |
| Lessons | `lessons/` | lessons learned, one file per domain |
| API | `api/` | rules and facts about internal APIs |
| Repos | `repos/` | one file per active repo, plus the map `repos/_index.md` |
| Experiments | `experiments/` | a map of the boards where experiments live. Per-experiment results stay in the tracker. Durable takeaways go to `lessons/` |
| Cards | `confluence/` | reference cards for durable internal pages |

## Hub kinds

A hub is `entities/<kind>-<slug>.md` with the tag `kind/<kind>`. Use one of these kinds. Add or remove kinds in `knowledge.json`.

| Kind | For |
|---|---|
| `org` | the organization itself and related business lines |
| `team` | a team or squad |
| `product` | a product, app or title |
| `service` | a running service, bot or pipeline |
| `metric` | a metric your organization invented or computes its own way. No hub for standard industry metrics |
| `vendor` | a supplier, network or other external vendor |
| `process` | a workflow |
| `initiative` | an initiative theme |
| `tool` | a warehouse tool in this repo (`tools/<name>/`) |
| `person` | a member, by name and role |
| `competitor` | a competing company or product |
| `platform` | a platform owner (an app store, an OS, a cloud) and its policies |
| `playbook` | a playbook the organization follows. The hub is a reference card: link to the source of truth, last-updated date, a short summary and the table of contents |
| `datasource` | a place where data or docs live: a database, a BI tool, an internal API, a tracker project set, a wiki space. The hub says what lies there, how to reach it (tool and env var names, never secret values), an index with one line per entry point, and the questions it answers. No live numbers |

A repo is not a hub. A repo has its own file `repos/<name>.md` with `type: repo`. See "Repo files".

A person hub is `entities/person-<slug>.md` with the tag `kind/person`. Its frontmatter has the keys listed in `person_required` of `knowledge.json` (default `title` and `department`). `github` (the login) and `slack` (the member ID) are optional. The hub never holds an email, a phone or any contact. When the `directory` family is set up, `scripts/knowledge_verify.py` compares `title` and `department` with your people directory.

A hub may carry `verified` (ISO date, the single freshness field). Only `scripts/knowledge_verify.py --apply` writes it, after an ok verdict. The run also writes `vault/_verify/<date>.json`. The gate rejects a date with no entry there.

A hub must carry `verify_with`, and `verify_query` unless `verify_with` is `none`. See "Self-verification".

Hub body convention:

- A short description.
- A metric hub holds only organization-specific content: the formula when it differs from the standard, thresholds, and lessons. No textbook definitions.
- A line `Related: [[hub-a]], [[hub-b]]` with links to other hubs.

The orphan rule: every entry in a domain, `lessons/` or `repos/` file links at least one hub in its body. Hubs, `_index`, `_template` and `_graveyard` are exempt.

## Vault and links

- The vault root is `plugins/knowledge/vault/`.
- A link uses the vault path without `.md`: `[[example/rules]]`.
- A link to one entry adds its block id: `[[example/rules#^R1]]`.
- A link to a hub uses the hub basename only: `[[vendor-example-vendor]]`.
- The gate resolves every link. A link to a removed id resolves through `_graveyard.md`.

## File kinds

| Vault path | `type` | Entries |
|---|---|---|
| `<domain>/rules.md` | `rule` | one per rule |
| `<domain>/knowledge.md` | `fact` | the leaf-section rule below |
| `<domain>/hypotheses.md` | `hypothesis` | the leaf-section rule below |
| `api/rules.md`, `api/knowledge.md` | `rule`, `fact` | a normal domain: internal APIs |
| `lessons/<domain>.md` | `lesson` | one per leaf section |
| `experiments/knowledge.md` | `fact` | the board map: the leaf-section rule below |
| `repos/<name>.md` | `repo` | the whole file is one repo note, see "Repo files" |
| `repos/_index.md` | `map` | the whole file is one note: the repo map |
| `confluence/<SPACE>.md` | `card` | one per Confluence page, see "Reference cards" |
| `entities/<kind>-<slug>.md` | `entity` | the whole file is one note (a hub) |
| `_index.md`, `lessons/_index.md`, `_graveyard.md`, `_template.md` | `map` | the whole file is one note |
| `_verify/<date>.json` | none | written by the script, never by hand |

**The leaf-section rule.** A leaf section is a heading with no deeper heading under it. If its body is only top-level bullets, each bullet is one entry. Any other leaf section is one entry. Fenced blocks are never read.

## File frontmatter

- Line 1 is `---`. The header ends at the next `---` line.
- Each line is `key: value`. No nested YAML, no block lists.
- A list is flow style: `[a, b]`.
- `id` is the vault path without `.md`. A hub `id` is its basename.
- `type` is the type from the table above.
- A hub also has `aliases`: names people use for it.

```yaml
---
id: example/rules
type: rule
tags: [domain/example]
---
```

## The meta line

One meta line per entry. It holds Dataview inline fields and ends with the block id.

- Syntax: one or more `[key:: value]` fields, a space, then `^<block-id>`.
- After a heading, the meta line goes on the next line.
- For a bullet, the meta line goes at the end of the first paragraph. Indent it.
- A value holds no single `[` or `]`.

| Field | Value |
|---|---|
| `added` | the date of the source, `YYYY-MM-DD`: the newest Slack message date, the Confluence page version date, or the issue date. Use the landing date only when the source has no date. The gate checks Slack dates |
| `source` | `issue/<n>`, `pr/<n>`, `github:<org>/<repo>`, `confluence:<page id>`, `slack:<ts>`, `jira:<KEY>` or `url:<https://…>` (a reference digest: the primary URL). A lesson lists every source id, comma-separated. A row hint may follow the page id: `confluence:<page id>#<hint>` |
| `origin` | lesson only: `slack`, `confluence`, `agent`, `issue` or `jira` (a finished experiment) |
| `author` | lesson only, optional: `name — role`. Never contacts |
| `verify_with` | required: `github`, `directory`, `confluence`, `slack` or `none` |
| `verify_query` | required unless `verify_with` is `none`: one query from the grammar below |
| `last_verified` | date of the last ok check. Only `scripts/knowledge_verify.py --apply` sets it, never a hand edit. The gate needs the entry id in `_verify/<date>.json` |
| `status` | `active` or `disputed`. A lesson uses `active`, `hypothesis` or `stale` |

Rule:

```markdown
## R1 — <Imperative rule title>
[added:: <YYYY-MM-DD>] [source:: issue/<n>] [verify_with:: github] [verify_query:: github:repo:<org>/<name>] [status:: active] ^R1
```

Fact:

```markdown
- <YYYY-MM-DD>: **<the fact in one sentence>**.
  [added:: <YYYY-MM-DD>] [source:: issue/<n>] [verify_with:: confluence] [verify_query:: confluence:<page id>] ^K-<slug>
```

Hypothesis:

```markdown
## H-<slug> — <Whether the claim holds>
[added:: <YYYY-MM-DD>] [source:: issue/<n>] [verify_with:: none] ^H-<slug>
```

Lesson (one entry per leaf section; `added`, `source`, `origin`, `status` and `verify_with` are required):

```markdown
## L-<slug> — <The takeaway as one sentence>
[added:: <YYYY-MM-DD>] [source:: slack:<ts>] [origin:: slack] [author:: <Name — Role>] [status:: active] [verify_with:: slack] [verify_query:: slack:<channel id>:<ts>] ^L-<slug>
```

## Repo files

Each active repo gets one file `repos/<name>.md`. A repo is active when it is not archived and has a push in the last 6 months. The map `repos/_index.md` lists them.

```markdown
---
id: repos/<name>
type: repo
tags: [domain/repos]
org: <org key from scripts/repo_catalog.json>
section: <product|tool|package|service|qa|data|other>
added: <YYYY-MM-DD>
verify_with: github
verify_query: github:repo:<org>/<name>
---
# <name>

<What the repo is and does.>

- GitHub: `<org>/<name>`
- Status: active

Related: [[service-<slug>]]
```

- `org` is a key of `orgs` in `scripts/repo_catalog.json`. `section` is one of the section keys in `scripts/repo_catalog.py`.
- The first paragraph says what the repo is and does. The map uses its first sentence.
- Optional bullets: Stack, Start reading, Env names (names only), Answers. Omit a bullet you cannot confirm.
- `Related:` links at least one `entities/` hub. The gate rejects a repo file over 2500 bytes.

## Ids

- The block-id charset is `A-Z`, `a-z`, `0-9` and `-`.
- A block id is unique in its file. The entry id is `<vault path>#^<block id>`.
- Rules: `^R<n>`. Facts: `^K-<slug>`. Hypotheses: `^H-<slug>`. Lessons: `^L-<slug>`. A repo file has no block ids.
- `<slug>` is lower case, words joined by `-`.
- A removed id is never reused. `_graveyard.md` holds it.

## Self-verification

Every hub and every entry names how to check itself against a live source. The gate requires `verify_with` on all of them. Write `none` when no check applies.

- A hub or a repo file puts `verify_with` and `verify_query` in its frontmatter. A team hub may use `verify_with: confluence` and `verify_query: confluence:<page id>` when a planning page is its source.
- An entry puts `[verify_with:: …]` and `[verify_query:: …]` on its meta line.
- `verify_query` holds one query. Join several with `;`. Every query must fit the `verify_with` family.

| `verify_query` | Checked by | ok when | gone when |
|---|---|---|---|
| `github:repo:<org>/<name>` | script (gh) | the repo exists, is not archived and had a push in the last 183 days | 404, archived, or no push in 183 days |
| `github:path:<org>/<repo>:<path>` | script (gh) | the file exists on the default branch | 404 |
| `directory:user:<github login>` | script (your people directory API) | the user has this GitHub account and a department that matches `directory.department_regex` | not found, or the department no longer matches |
| `directory:user-name:<Full Name>` | script | same, matched by name (a person with no GitHub) | same |
| `confluence:<page id>` | cron agent | the page exists and still states the claim | the page is deleted or contradicts the claim |
| `slack:<channel id>:<ts>` | cron agent | the message exists | the message is deleted |
| `none` | nobody | not applicable | not applicable |

The `directory` family needs the env vars named in `directory.base_env` and `directory.key_env` of `knowledge.json`. Without them the script returns `error` for these queries.

A script verdict is `ok`, `changed`, `gone`, `manual` or `error`. `changed` means the title or department of a person, or the description of a repo, differs from the record. The report is advisory for `changed`.

`scripts/knowledge_verify.py --apply` sets `last_verified` (entries) or `verified` (hubs) on `ok` only. It never edits content. The cron agent turns `changed` and `gone` verdicts into edits and graveyard removals. See `docs/cron-contract.md`.

## Reference cards

A card points to one durable Confluence page. It never copies the page. The file is `confluence/<SPACE>.md` with `type: card`. The block id is `^page-<id>` and the digits equal the page id in `source`.

Each card has:

- A heading with the page title.
- A meta line `[added:: <date>] [source:: confluence:<id>] [updated:: <version date>] [verify_with:: confluence] [verify_query:: confluence:<id>] ^page-<id>`.
- The page URL.
- One line `Sections: a, b, c` from the page headings.
- At most 5 summary lines, and one `[[hub]]` link.

The gate rejects a card over 900 bytes, a card with a table row that holds a digit, and a card with another `verify_with`. Only `scripts/knowledge_verify.py` writes `verified` or `last_verified`. `scripts/vault_plan.py` is the only writer for job output.
