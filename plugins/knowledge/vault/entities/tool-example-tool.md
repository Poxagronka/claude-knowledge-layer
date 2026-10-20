---
id: tool-example-tool
type: entity
tags: [kind/tool]
aliases: [example-tool]
verify_with: none
---
# Example Tool

A warehouse tool lives in `tools/<name>/` of this repo. The hub names the folder and how to get it.

- Path: `tools/example_tool/`. It has a `Makefile` with a `check` target.
- Get it: fetch only that folder with `gh api` or a sparse clone. Never clone the whole repo into a project.
- Use: run `make check` in the folder.

Related: [[process-example-process]], [[team-example]]
