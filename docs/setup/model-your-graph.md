# Step 3: Model your graph

This step decides what your vault holds and how it is shaped. Do it with care. The shape controls what Claude finds and what it never sees.

Contents: [Pick hub kinds](#pick-hub-kinds), [Write the owner rules](#write-the-owner-rules-and-scope), [Map your organization](#map-your-organization), [What not to store](#what-not-to-store), [Replace the stub vault](#replace-the-stub-vault), [Verify families](#verify-families), [Directory check](#wire-the-directory-check-or-remove-it).

## Pick hub kinds

A hub is one note for one node of your organization. The file is `plugins/knowledge/vault/entities/<kind>-<slug>.md` and it has the tag `kind/<kind>`.

`hub_kinds` in `plugins/knowledge/knowledge.json` lists the allowed kinds and the maximum size of a hub in bytes. The gate `scripts/check_knowledge_graph.py` reads both from this file.

```json
"hub_kinds": {"org": 8500, "team": 5500, "person": 2500, "product": 1500, "service": 2000}
```

Rules for the list:

1. Keep a kind only when you will write at least three hubs of it. A kind with one hub is a section in another hub.
2. A size cap stops a hub from growing into a document. Keep a cap low. Move detail to a domain file and link it.
3. Add a kind by adding a key. A kind name uses lower case letters only. Remove a kind by deleting the key and its hubs. The gate fails on a hub with an unknown kind.
4. Keep `org` and `person`. The root hub is an `org` hub, and people need a place.
5. `person_required` lists the frontmatter keys of a person hub. The default is `title` and `department`. `github` and `slack` are optional keys.

The template ships these kinds. Use the table to decide.

| Kind | Holds | Typical count |
|---|---|---|
| `org` | The organization and its business lines | 1 to 5 |
| `team` | A team or squad, its DRI and its scope | One per team |
| `person` | Name, role, department | One per person who owns something |
| `product` | A product, app or title | One per product |
| `service` | A running service, bot or pipeline | One per system |
| `metric` | A metric you define in your own way | A few |
| `vendor` | A supplier or external partner | One per vendor you work with daily |
| `process` | A workflow, such as release or incident | A few |
| `initiative` | A theme with a reason and an outcome | Active ones |
| `tool` | A tool in the `tools/` warehouse | One per tool |
| `competitor` | A competing company or product | A few |
| `platform` | A platform owner and its policies | A few |
| `playbook` | A reference card for a playbook | A few |
| `datasource` | Where data or docs live | One per system of record |

## Write the owner rules and scope

Two places say what belongs in the vault.

1. `scope` in `knowledge.json`. One paragraph. The selector and capture prompts use it to decide if a fact is yours. Example: "Knowledge specific to Acme Corp: its teams, products, services, metrics, vendors, processes and decisions. No generic tool facts."
2. The "What belongs here" section of `plugins/knowledge/vault/_template.md`. A numbered list of owner rules. The PR knowledge review reads it, and so do your people. Edit the list. Keep the numbers stable, because other files cite them.

Write rules that a reviewer can apply in ten seconds. Good owner rules answer these questions:

- What is in scope? Name the kinds of fact you want, such as definitions, decisions with reasons, owners and "where to find X".
- What is out of scope? Generic tool facts, public definitions, live numbers.
- How are numbers handled? The template allows a number only with "as of <date>" and a source.
- How are people handled? The template allows name, role and department. No contacts.
- Which external docs may you digest? The template allows one applicable fact per entry, with a URL and an as-of date.

Then write the entry points.

- `vault/_index.md`: a short map. State your scope, list the categories, and name the root hub.
- The root hub: rename `entities/org-example.md` to your root hub id, for example `entities/org-acme.md`. Set `id` in its frontmatter and set `root_hub` in `knowledge.json` to the same id. The session header tells Claude to start at this hub. Link every team, product and main system from it.

## Map your organization

Build the graph from the top. Write the root hub, then the layer below, then the details. Stop when a new hub would repeat what a Confluence page or a repo README already says well.

| Node | Hub kind | What to write | Where to get the truth |
|---|---|---|---|
| Company, business line | `org` | One paragraph, the list of teams, the main products | Your org chart page |
| Team, squad | `team` | Mission, DRI, what the team owns, channels | Org chart, planning page |
| Person | `person` | Name, title, department. Link the team | People directory |
| Product | `product` | What it is, who owns it, main services | Product docs |
| Service, bot, pipeline | `service` | Purpose, owner, how it fails, where the code lives | Repo, runbook |
| Metric | `metric` | Formula when it differs from the standard, thresholds, lessons | Metric spec |
| Vendor | `vendor` | What you buy, contract owner, known policy limits | Vendor docs |
| Process | `process` | Steps, owner, entry and exit | Process doc |
| Initiative | `initiative` | Reason, outcome, owner, state | Planning tool |
| Data source | `datasource` | What lies there, how to reach it (tool and env var names), questions it answers | The system itself |

Then write facts and rules in domain files.

- A domain is one topic with three files: `rules.md`, `knowledge.md`, `hypotheses.md`. See `vault/example/` for the layout.
- Trust order is rules, then knowledge, then hypotheses.
- Every entry links at least one hub. The gate fails on an orphan.
- Write lessons in `lessons/<domain>.md`. A lesson has an origin: `slack`, `confluence`, `agent`, `issue` or `jira`.
- Use `repos/<org>.md` for the catalog of active repos. Generate it, see below.
- Use `confluence/<SPACE>.md` for reference cards. A card holds the link, freshness, the section list and a short summary. It never copies the page.

Generate the repo catalog. Edit `scripts/repo_catalog.json` first: set `orgs`, `name_filter`, `skip`, `default_hub` and the `repos` map from repo name to an object with `type` and `hubs`, for example `"repos": {"example-service": {"type": "service", "hubs": ["team-example"]}}`. Then run:

```bash
python3 scripts/repo_catalog.py --today 2026-10-20
python3 scripts/repo_catalog.py --today 2026-10-20 --check
```

The script lists repos with `gh`. It keeps repos that are not archived and had a push in the last 183 days. A purpose line you write by hand wins over the GitHub description.

## What not to store

The vault is shared with every teammate and with every session. Keep these out.

| Do not store | Why | Store this instead |
|---|---|---|
| Live metrics and finance numbers (revenue, spend, KPI values) | They change daily and the vault goes stale | A `datasource` hub that says where the live number lives |
| Secrets, tokens, keys, passwords | The vault is plain text in git | The env var name and who issues it |
| Contacts (emails, phones, home addresses) | Privacy | Name, role, team and the Slack channel name |
| Personal or sensitive data (HR, health, money of a person, opinions about people) | Privacy and law | Nothing |
| Confidential partner terms | Contract risk | A link to the contract owner |
| Generic tool facts (how Git works) | Claude knows them | Only your own convention |
| Copies of wiki pages | They drift | A reference card with the link |
| Per-experiment results | They live in the tracker | A map of boards and the lessons that last |

The capture gate and `scripts/vault_plan.py` drop text that looks like a token, a key, a JWT, a password or an email. Do not rely on that gate alone. Review text before it enters the vault.

## Replace the stub vault

The template vault holds a fictional organization. Replace it in this order. Run the gate after each group.

1. Pick the root hub. Rename `entities/org-example.md` and set `root_hub` (see above).
2. Rename each stub hub to a real node and rewrite its text. The stub ids are `team-example`, `person-jane-doe`, `product-example-app`, `service-example-service`, `metric-example-metric`, `vendor-example-vendor`, `process-example-process`, `initiative-example-initiative`, `tool-example-tool`, `competitor-example-competitor`, `platform-example-platform`, `playbook-example-playbook` and `datasource-example-datasource`.
3. Delete a stub kind you do not use, with its `hub_kinds` key.
4. Replace `example/` with your first real domain. Rename the folder and the `id` and `tags` in its three files.
5. Rewrite `api/`, `lessons/example.md`, `repos/example-org.md`, `experiments/knowledge.md` and `confluence/EXAMPLE.md`. Delete the files you do not need and fix the links in `_index.md`.
6. Fix every `[[link]]` that points to a removed id. A moved id goes to `_graveyard.md`.
7. Replace the eval cases (see [maintain.md](maintain.md)).

Search for leftovers:

```bash
grep -rniE "example|jane doe|acme" plugins/knowledge/vault evals | head
python3 scripts/check_knowledge_graph.py
```

The stub hubs use `verify_with: none`, so the live check cannot mark them `gone`. When you write a real hub, replace `none` with a real check where one exists.

## Verify families

Every hub and every entry names a live check in `verify_with`. The grammar of `verify_query` is in `_template.md`. The families are:

| Family | Checked by | Example query |
|---|---|---|
| `github` | `scripts/knowledge_verify.py` through `gh` | `github:repo:<org>/<name>`, `github:path:<org>/<repo>:<path>` |
| `directory` | `scripts/knowledge_verify.py` through your people directory | `directory:user:<github login>`, `directory:user-name:<Full Name>` |
| `confluence` | A maintainer agent with Confluence access | `confluence:<page id>` |
| `slack` | A maintainer agent with Slack access | `slack:<channel id>:<ts>` |
| `none` | Nobody | none |

Pick the strongest family that fits. A repo record uses `github`. A person uses `directory`. A claim from a wiki uses `confluence`. Use `none` only for knowledge that has no external source, such as a decision.

Run the checks:

```bash
python3 scripts/knowledge_verify.py --oldest 60 --out report.json
```

The report gives a verdict per record: `ok`, `changed`, `gone`, `manual` or `error`. Add `--apply` to stamp `last_verified` on `ok` records. The script is read-only without `--apply`.

## Wire the directory check, or remove it

The `directory` family compares person hubs with your people directory. It needs a read-only HTTPS endpoint and a key.

Set the names in `knowledge.json`:

```json
"directory": {
  "base_env": "DIRECTORY_API_BASE",
  "key_env": "DIRECTORY_API_KEY",
  "users_path": "/api/v1/users",
  "department_regex": "^(Engineering|Product|Design)"
}
```

| Key | Meaning |
|---|---|
| `base_env` | Name of the env var that holds the base URL. It must start with `https://` |
| `key_env` | Name of the env var that holds the key. The script sends it as `Authorization: Bearer <key>` |
| `users_path` | Path of the users list. The script makes one GET per run |
| `department_regex` | A regex a department must match to count as "in the organization". A user outside it gives `gone` |

The endpoint must return a JSON list of user objects. Each object has these string fields:

```json
[
  {"github": "jane-doe-example", "name": "Jane Doe", "title": "Engineering Manager", "department": "Platform"}
]
```

- `github` is the GitHub login. `directory:user:<login>` matches it, ignoring case.
- `name` is the full name. `directory:user-name:<Full Name>` matches it.
- `title` and `department` are compared with the person hub. A difference gives `changed`.
- An empty list or a non-list reply gives `error`.

Your directory may not look like this. Put a small adapter in front of it that returns this shape. Do not send secrets in the URL. The script follows no redirects and never prints the key.

Set the env vars in the shell that runs the check, or in a file you pass with `--env-file`. Keep that file out of git.

To remove the check, do three things:

1. Delete the `directory` key from `knowledge.json`.
2. Change every `verify_with: directory` to `none` in the vault.
3. Run `python3 scripts/build_integrity.py` and the gate.

Without a directory, nobody tells you when a person leaves. Plan another way to review person hubs, such as a quarterly manual pass.
