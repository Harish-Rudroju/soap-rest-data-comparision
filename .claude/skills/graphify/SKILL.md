---
name: graphify
description: Use the optional Graphify code graph first for repository structure and relationship questions, then verify details in source. Fall back cleanly when unavailable or stale.
---

# Graphify-first navigation

Read `PROJECT.md` before navigating this repository.

For architecture, dependencies, imports, inheritance, callers/callees,
execution paths, and component relationships:

1. Check that `graphify-out/graph.json` exists and resolve the CLI. Use
   `graphify` when it is on `PATH`; on Windows, also check
   `$env:USERPROFILE\.local\bin\graphify.exe`.
2. Query the graph first with focused commands such as:
   - `graphify query "<structural question>" --graph graphify-out/graph.json`
   - `graphify explain "<symbol>" --graph graphify-out/graph.json`
   - `graphify path "<symbol A>" "<symbol B>" --graph graphify-out/graph.json`
3. Use the returned symbols and source locations to choose which real files to
   inspect. Read source before asserting exact implementation behavior or
   editing code.
4. If the CLI or graph is missing, a query fails, or results conflict with the
   current source, immediately fall back to `PROJECT.md`, the other repository
   instructions, `rg`/`grep`/`find`, and direct file reading.

Graphify is optional tooling. Never add it to application dependencies or make
build, run, test, or normal development workflows depend on it.
