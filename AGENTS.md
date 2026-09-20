# Read PROJECT.md first

Before doing anything else in this repo, read **`PROJECT.md`** in full. It
is the maintained, code-verified architecture and design reference for
this project (Turing Engine — a SOAP vs REST data comparison tool) —
wizard flow, comparison-logic system, report format, frontend notes,
known-shelved features, everything.

There is no other spec document — `PROJECT.md` is it. **Non-negotiable
rule (spelled out in full in `PROJECT.md` §0): any feature, behavior
change, bug fix, or design decision you make must update `PROJECT.md` in
the same change.** The user may come back to this project later with a
different agent entirely — this file is that future agent's only way of
knowing what happened. An undocumented change may as well not exist.

# Optional Graphify-first code navigation

Graphify is optional developer tooling on this branch. After reading
`PROJECT.md`, prefer Graphify as the first navigation tool for questions about
architecture, dependencies, imports, inheritance, callers/callees, execution
paths, or relationships between components when both the Graphify CLI and
`graphify-out/graph.json` are available.

Use Graphify to identify the relevant components and files, then inspect the
actual source before relying on exact behavior or making code changes. If
Graphify is unavailable, fails, or appears stale, continue normally with this
repository's Markdown instructions and standard tools such as `rg`, `grep`,
`find`, and direct file reading. Graphify must never be treated as a build,
test, runtime, or contributor prerequisite.
