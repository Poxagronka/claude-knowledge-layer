# Security Policy

## Supported versions

The `main` branch gets security fixes. Older tags do not.

## Report a vulnerability

Do not open a public issue for a vulnerability.

Use GitHub private vulnerability reporting:

1. Open the [Security Advisories page](https://github.com/Poxagronka/claude-knowledge-layer/security/advisories/new) of `Poxagronka/claude-knowledge-layer`.
2. Describe the problem, the affected files and the steps to reproduce it.
3. Send the report.

The report stays private between you and the maintainer. The maintainer answers within 7 days and works with you on a fix and a disclosure date.

## What counts

- A path where vault text or issue text runs as an instruction.
- A path where the plugin sends secrets, prompts or code off the machine.
- A flaw in the secret filter, the install of the local ranker, the self-update or the integrity check.
- A flaw in a CI workflow that lets a pull request run with secrets.

## What does not count

- Findings that need a malicious `knowledge.json` or a malicious fork. The owner of a fork controls its plugin.
- Issues in a vault that you filled with your own content.

## Note for forks

A fork has its own `issues_repo` and its own secrets. Report a problem in your own fork to your own team. Report a problem in the template here.
