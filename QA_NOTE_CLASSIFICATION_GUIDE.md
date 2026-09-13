# QA Note Classification Guide

## Purpose

This file is a **lookup catalog** that maps known QA Note patterns (the
freeform testing notes a QA person writes per field, e.g. "First Name/Last
Name should only be populated for Person records") to the structured codes
the comparison tool actually understands: `Comparison Logic`, `Evidence
Logic`, and `Testing Size`.

It exists so that **any tester on the team** — using any agent (Claude,
Codex, or a human doing it by hand) — can take a raw sheet of just
`Field Name` + `QA Notes` (the plain-English notes as written today) and turn
it into a finished **Field Metadata sheet** the comparison tool consumes,
without having to re-derive the comparison logic from scratch or ask an
engineer every time. This file is the single source of truth for that
mapping — extend it (see "How to extend this catalog" below) as new note
patterns show up on new objects.

## Input / Output contract for an agent using this guide

**Input**: a sheet with two columns: `Field Name`, `QA Notes` (raw freeform
text, as currently written by QA — see REQUIREMENTS.md §4.2 background).

**Output**: a Field Metadata sheet with four columns, matching what the
comparison tool expects:

| Column | Value |
|---|---|
| `Field Name/Label` | Copied as-is from the input sheet. |
| `Comparison Logic` | A code from the catalog below (§ Code Catalog). |
| `Evidence Logic` | A code from the catalog below. |
| `Testing Size` | A number (e.g. `5`) or `FULL`, per the catalog guidance for the matched pattern, or the team's default N if no pattern implies otherwise. |

**Matching procedure**, for each row in the input sheet:

1. Read the `QA Notes` text for this field.
2. Compare it against the **Note Patterns** listed in the Code Catalog below
   — match on meaning, not exact wording (QA notes are freeform, so the same
   underlying rule may be phrased differently across fields/objects).
3. If a confident match is found, assign that pattern's `Comparison Logic` /
   `Evidence Logic` / `Testing Size`.
4. If **no existing pattern matches confidently**, do **not** guess a new
   comparison behavior. Assign the safe defaults — `Comparison Logic =
   STRICT`, `Evidence Logic = RANDOM`, `Testing Size` = the team's default N
   — and **flag the row** (e.g. in a comment/output note: "No matching
   pattern — used defaults, needs review") so a human can decide whether
   this is a genuinely new pattern that belongs in this catalog, or just
   explanatory text that doesn't change comparison behavior.
5. When a genuinely new pattern is confirmed, add it to the Code Catalog
   below (see "How to extend this catalog") so future runs recognize it
   automatically — this catalog is expected to grow over time as more of the
   20+ objects are onboarded.

## Code Catalog

Each row below is one recognized QA Note *pattern* (a category of note
meaning, not a literal string) and the codes/behavior it maps to.

> Status: scaffold only — populated with the patterns already discussed and
> agreed on during Phase 1 planning. Awaiting the real QA Notes list to fill
> this out with the ~10-20 actual unique note types used in production.

| Note Pattern (meaning) | Comparison Logic | Evidence Logic | Testing Size | Comment template(s) |
|---|---|---|---|---|
| No special note / plain field, exact value expected | `STRICT` | `RANDOM` | team default N | Verified: *(blank)*. Mismatch, one side blank: `"<SIDE> is blank, <OTHER SIDE> has a value"`. Mismatch, both have values: `"Values differ"`. |
| Explanatory note about formatting that doesn't change comparison (e.g. "copied as text, leading zeros kept") | `STRICT` | `RANDOM` (or `PATTERN:<regex>` if the note names a specific pattern that should be proven, e.g. leading zeros — see below) | team default N | Same as `STRICT` above — the note is informational only, comparison behavior is unchanged. |
| Field only expected to be populated for a specific record type; must be blank otherwise (e.g. First/Last Name for Person vs Company) | `CONDITIONAL-PERSON` *(rename per actual condition once real notes are reviewed)* | `RANDOM` | team default N | Mismatch (violation): `"Expected blank for <OTHER TYPE> record but <SIDE> returned a value"`. Verified: `"Correctly populated for <TYPE> record"` / `"Correctly blank for <OTHER TYPE> record"`. |
| Multi-value / picklist field where order may differ | `PICKLIST-ORDER` | `RANDOM` | team default N | Mismatch (order only): `"Order mismatch — same values, different sequence"`. Mismatch (values differ): `"Values differ"`. |
| Date/timestamp field, SOAP and REST format differently | `DATE-COMPARISON` | `RANDOM` | team default N | Mismatch: `"Date differs, time matches"` / `"Time differs, date matches"` / `"Values differ"` (when both differ), always still `Mismatch` per strict-compare (see REQUIREMENTS.md §6.3 — no automatic leniency). |
| Note describes a specific data pattern that must be proven to survive the round-trip (e.g. leading zeros on Zip) | `STRICT` | `PATTERN:<regex>` (e.g. `PATTERN:^0` for leading zeros) | `FULL` (recommended — random N-sampling may never select a record exhibiting the pattern; see REQUIREMENTS.md §9.2) | Same as `STRICT`, plus the `FULL`-mode summary comment ("Compared all N matched records — ..."). |

## How to extend this catalog

When a new, genuinely distinct QA Note pattern is found:

1. Add a new row to the Code Catalog table above, describing the pattern's
   **meaning** (not a verbatim quote — patterns should generalize across
   fields/objects where the same underlying rule applies).
2. Assign or reuse a `Comparison Logic` code. Reuse an existing code if the
   comparison behavior is genuinely the same; only introduce a new code name
   for genuinely new comparison behavior.
3. Assign or reuse an `Evidence Logic` code, same principle.
4. Decide `Testing Size` guidance: default N unless the pattern specifically
   needs full-scan proof (like the leading-zeros case).
5. Write the Comment template(s) for each outcome (Verified / Mismatch, and
   any sub-cases like "order mismatch" vs "values differ").
6. If the `Comparison Logic` or `Evidence Logic` code doesn't exist as a
   function in `comparison_engine.py` yet, it needs to be implemented there
   too — this catalog defines the *contract* (what the code means and what
   it should output), the engine implements it.

## Status

This file starts as a scaffold with the patterns already reasoned through
during planning. It should be filled in with the real ~10-20 unique QA Note
types (per REQUIREMENTS.md §4.2) once shared, and grown further as new
objects surface new note patterns.
