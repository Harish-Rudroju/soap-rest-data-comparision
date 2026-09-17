# Turing Engine — Project Reference

**Tagline:** Compare · Validate · Migrate

This file is the single source of truth for how this project works, why it's
built the way it is, and what's still open. It replaces the old
`REQUIREMENTS.md` / `TODO.md` (which described an earlier, much simpler
version of the tool and had drifted badly out of date). If you're an AI
agent (Claude, Codex, Roo Code, etc.) or a human picking this project back
up, read this file first — it should let you continue building without
having to reverse-engineer intent from the code alone.

There is no separate requirements/spec document anymore — this **is** the
spec, kept in sync with the actual code.

---

## 0. Maintenance rule — non-negotiable, read this first

**Every time you implement a new feature, change existing behavior, fix a
non-trivial bug, or make a deliberate design decision anywhere in this
project, you must update this file in the *same* change — not "later,"
not "next session."** This is exactly the discipline that
`REQUIREMENTS.md`/`TODO.md` lacked, which is why they had to be scrapped
and replaced with this file in the first place (see §1). Don't repeat that
mistake with `PROJECT.md` itself.

Concretely, that means:

- Added a wizard step, route, or checkbox? Update §3 (flow diagrams, the
  skip-condition table, the `PENDING` shape) and §11 (route table).
- Added or changed a Comparison Logic type? Update §6 (the type table,
  the dispatch order if it changed, the comment templates) and §8.1 if it
  affects condensing/dashboard behavior.
- Changed a report's columns, caps, or exclusions? Update §8.
- Made a frontend/design decision, especially one that reverted an
  earlier attempt or discovered a gotcha? Update §10 — the gotchas
  (§10's SVG/CSS lessons, the Jinja-in-HTML-comment recursion bug in §9)
  are exactly the kind of thing worth capturing so nobody re-discovers
  them the hard way a second time.
- Shelved or explicitly rejected an idea instead of building it? Add it
  to §6.8 (or wherever the equivalent "deferred" list is) with the reason
  it was rejected, not just silence — silence reads as "never considered,"
  which invites someone to propose it again from scratch.
- Removed a file, route, or feature? Delete or rewrite the section that
  described it — don't leave a stale reference to something that no
  longer exists (this happened once already with dangling
  `REQUIREMENTS.md` references left in code docstrings after that file
  was deleted; both were fixed in the same pass that deleted the file).

The point of this rule: the user may step away from this project for a
long stretch and come back later, possibly working with a different AI
agent entirely. That agent's *only* way of knowing what happened while the
user was away is this file — there's no other persistent memory of this
project's history. If a change isn't reflected here, it may as well not
be documented at all.

---

## 1. What this tool is for

A company is migrating its NetSuite↔Salesforce middleware from SOAP to
REST. Salesforce's objects and field names don't change — only the data
source does — but REST can return values in a different shape than SOAP
did (different date formats, different casing, reordered multi-select
values, etc.), which can silently break things that depended on the old
SOAP data shape.

QA's job is to take a SOAP-connected export and a REST-connected export of
the same Salesforce object (Customer, Item, Sales Order, and 20+ more to
come) and diff them field-by-field, record-by-record, producing evidence
that a human can act on. This tool automates that diff, generically across
any object, so it's reusable every time the middleware changes.

**Non-goals:** this is a local, single-user QA tool. It is explicitly not
built for concurrent multi-user production hosting (see §9, Hosting
caveats).

---

## 2. Architecture at a glance

```
app.py                    Flask routes + wizard flow control (no comparison logic here)
comparison_engine.py       All comparison logic, pure Python/pandas, no Flask dependency
templates/                 One Jinja template per wizard page (no shared base layout —
                            each file is a full page; a few shared partials are
                            {% include %}'d, see §8)
static/style.css            Main stylesheet (design tokens, light/dark theme, all page styles)
static/css/engine-animation.css   Isolated stylesheet for the cinematic Start button (see §10)
static/js/engine-animation.js     Isolated JS for the same
static/images/               Logo assets
QA_NOTE_CLASSIFICATION_GUIDE.md   Reference for turning raw QA notes into Comparison Logic
                                   assignments (see §6.7) — kept separate from this file
                                   because it's a lookup catalog, not architecture doc.
README.md                  Setup/run/usage instructions for a human — this file (PROJECT.md)
                            is the technical reference; README.md doesn't duplicate it.
```

**No database.** Two in-memory dicts in `app.py` hold all server-side
state:

- `PENDING`: `pending_id -> {...}` — one entry per in-progress wizard
  session (uploaded file paths, chosen key, ignored fields, column
  mapping, field logic assignments, all the Advanced Options checkboxes,
  etc.). Created on upload, read/written at every wizard step, read one
  final time by `/run`.
- `RUNS`: `request_id -> {...}` — one entry per *completed* run, holding
  the generated report file paths so `/download/<request_id>/<which>` can
  serve them after the results page has rendered.

Neither dict is ever pruned. Uploaded files and generated reports live in
per-request `tempfile.mkdtemp()` directories that are never cleaned up
either. Fine for a single local user in a short session; would need real
cleanup/expiry if ever exposed more broadly.

---

## 3. The wizard flow

### 3.1 Steps, in order

```
Upload → Map Unmatched Fields → Select Matching Key → Drop Fields →
Field Comparison Logic → Ready to Run → Results
```

(Negative Case Testing is a second flow, selected via a tab on Upload —
see §7. It shares Map/Key/Drop but skips Field Comparison Logic entirely.)

Every step after Upload can be **shown or skipped**, per an "Always show
X" checkbox in Advanced Options (see §5). Two of the four steps also have
an *automatic* need-detection that forces them to show even if their
checkbox is unchecked:

| Step | Checkbox (default) | Auto-shows even if unchecked, when... |
|---|---|---|
| Map Unmatched Fields | `always_show_mapping` (**unchecked**) | some SOAP/REST columns don't share a name (`needs_mapping`) |
| Select Matching Key | `always_show_key` (**unchecked**) | no `Internal Id`-like column found in both sheets (`has_default_key` is false) |
| Drop Fields | `always_show_drop` (checked) | never — pure user choice, no way to detect "need" |
| Field Comparison Logic | `always_show_field_logic` (checked) | never — pure user choice; **compare-flow only**, see §7 |

**`always_show_mapping`/`always_show_key` default unchecked** — deliberately
different from the other two. Since both steps already auto-show whenever
they're actually needed (`needs_mapping`/`has_default_key` above), leaving
them checked by default meant they showed up on *every* run even when
there was nothing to map and a default key was found — pure friction. Both
the index page checkbox (`{{ 'checked' if cp and cp.always_show_mapping
else '' }}` — only checked if a prior submission set it true, not just
because there's no prior submission at all) and the `pending.get(...,
False)` fallback in `app.py` were changed together; `always_show_drop`/
`always_show_field_logic` have no auto-detection to fall back on, so they
stay checked by default.

### 3.2 Forward flow: the `_advance_after_*` chain

Each step's confirm route calls the next step's "advance" function, which
decides show-vs-skip and either renders that page or recurses into the
next `_advance_after_*`:

```
_advance_after_upload   (called by /compare, /negative-test)
  → _advance_after_mapping   (called by /confirm-mapping)
    → _advance_after_key     (called by /confirm-key)
      → _advance_after_drop  (called by /confirm-drop)
        → _render_options    (Ready to Run — final step, nothing to advance to)
```

If every skippable step's condition says "skip," a single `/compare` POST
can render the Results page in one hop — verified working, see §9.

### 3.3 Backward flow: the `_retreat_to_*_or_earlier` chain

Back buttons are **not** naive "go to the previous route" — going Back must
skip over any step that would have been auto-skipped going forward,
otherwise Back would reveal pages the user explicitly asked to hide. Each
retreat function mirrors the advance chain in reverse:

```
_retreat_to_field_logic_or_earlier   (Options' Back button, route /back-to-field-logic)
  → _retreat_to_drop_or_earlier        (route /back-to-drop)
    → _retreat_to_key_or_earlier         (route /back-to-key)
      → _retreat_to_mapping_or_earlier     (route /back-to-mapping)
        → _render_upload_with_prefill        (route /back-to-upload — always shown, no further retreat)
```

Each step's own template hardcodes its Back button's `formaction` to the
**specific route matching its position** (e.g. Drop Fields' Back always
posts to `/back-to-key`) — the *route itself* does the skip-cascading, not
the button. This means a template's Back button target never needs to
change even as skip settings vary between sessions.

**This was a real bug once** (see conversation history): initially, Back
routes always showed their one specific page unconditionally, so
unchecking all the "Always show" boxes made forward navigation skip
everything, but Back would still reveal every step one at a time. Fixed by
making every retreat function re-check the same skip condition the forward
advance function used.

### 3.4 State carried in `PENDING[pending_id]`

```python
{
    "flow": "compare" | "negative",
    "soap_path": str, "rest_path": str,        # temp file paths
    "object_name": str,
    "soap_namespace": str, "rest_namespace": str,
    "default_n": int, "full_by_default": bool,  # compare-flow only
    "always_show_mapping": bool,
    "always_show_key": bool,
    "always_show_drop": bool,
    "always_show_field_logic": bool,             # compare-flow only
    "key_columns": list[str],                    # set by /confirm-key
    "column_mapping": {soap_field: rest_field},  # set by /confirm-mapping
    "ignored_fields": list[str],                 # set by /confirm-drop
    "field_logic": {field_label: "DATE"|"PICKLIST"|"NUMBER"|"STRICT"},  # set by /confirm-field-logic
}
```

When a user edits settings and resubmits Upload **without re-choosing
files** (`existing_pending_id` hidden field, browsers can't pre-fill
`<input type=file>` for security reasons), `key_columns` / `ignored_fields`
/ `column_mapping` / `field_logic` are explicitly carried forward from the
old `PENDING` entry into the new one — see the `if existing and not
files_replaced:` blocks in `/compare` and `/negative-test`.

---

## 4. Data loading & column normalization

All of this happens once, right after upload, before anything else:

1. **`load_sheet`**: reads CSV (`_read_csv_with_fallback_encoding`, trying
   `utf-8-sig` → `cp1252` → `latin-1`) or Excel, **always as `dtype=str`,
   `keep_default_na=False`** — this is deliberate and load-bearing:
   without it, pandas would coerce things like leading-zero ZIP codes or
   large ID numbers into numeric types and silently mangle them. Malformed
   CSVs (`ParserError`/`EmptyDataError`) are caught and re-raised as a
   friendly `ValueError` shown on the upload page.
2. **`normalize_namespaced_columns`**: strips each side's Salesforce
   managed-package namespace prefix from every header, **anywhere it
   appears** (`header.replace(f"{namespace}__", "")`, not
   `.startswith()`) — this also fixes relationship-traversal fields like
   `Bill__r.breadwinner_ns__InternalId__c` → `Bill__r.InternalId__c`, where
   the namespace sits after a `.` rather than at the very start.
3. **`normalize_key_column_names`**: if both sides have a
   internal-id-like column (matched via `DEFAULT_KEY_ALIASES`, normalized
   through `_normalize_header` which lowercases and strips all
   non-alphanumerics — so `"Internal ID"`, `"InternalId"`, `"internal_id"`,
   `"InternalId__c"` all match), renames it to the canonical `"Internal
   Id"` on both sides.
4. **`apply_column_mapping`**: renames REST-side columns to their paired
   SOAP-side name, per the user's choices from the Map Unmatched Fields
   page. Reuses the same canonical-rename pattern as step 3 — downstream
   code never needs to special-case a mapped field, it's just an ordinary
   shared column from here on.

After these four steps, `diff_columns` / `common_columns` / `build_reports`
all just work on "does this header string exist in both DataFrames" — no
further namespace/mapping logic anywhere else in the codebase.

### 4.1 Composite keys

`key_columns` is a list (usually `["Internal Id"]`, but the user can pick
multiple columns via the Select Matching Key page). `_composite_key_series`
builds the actual per-row match key:

- **Single key column**: used as-is — this is why `Evidence` in the
  report usually just shows a plain Internal Id.
- **Multiple key columns**: joined into a labeled, human-readable string,
  e.g. `"Field: value, Field2: value2"`, so the report still tells you
  which record it is even without a single ID column.

**Picking a valid key — and the failure mode when it's wrong.** The tool
never checks key uniqueness — if the chosen key column(s) aren't actually
unique per row, `_composite_key_series` still produces a key, matching
degenerates into many-to-one/many-to-many, and rows from completely
unrelated records get compared as if they were the same record.

Symptom seen in practice: comparing NetSuite Sales Order **line items**
using `ItemInternalId__c` alone as the key — that field identifies the
*item/SKU*, not the line, so one item sold on hundreds of different order
lines collapses into one "record." The condensed report showed identical
`Evidence` example values recurring across otherwise-unrelated groups
(e.g. the same values appearing under both a `Verified` bucket and an
`Unverified` bucket for the same field) — that recurrence is the tell that
the key isn't unique, not a bug in the comparison/condensing logic.

Line-item-level objects usually have **no single unique column** in a flat
export (no `Internal Id`, no `ExternalId__c`) — `Name`/`Line__c` is just
the line number (reused across every parent record) and
`ItemInternalId__c` identifies the item, not the line. The fix is a
**composite key** of parent-record identifier + line number, e.g.
`Sales_Order__r.Name` + `Line__c` — check both sides for near-zero
duplicates on that composite (`Sales_Order__r.Name + "||" + Line__c`)
before trusting the run. A handful of leftover duplicates after that is
usually a genuine data artifact (e.g. a re-added line), not a key-choice
problem.

### 4.2 Blank handling

`is_blank(value)`: `None`, empty string, or (case-insensitive, stripped)
one of `{"", "null", "na", "n/a", "-"}`.

### 4.3 Map Unmatched Fields — page mechanics

This page (`map_columns.html`) is what produces `column_mapping` (§3.4).
Server-side (`_render_map_columns` in `app.py`), `engine.diff_columns` is
called with an *empty* `key_columns` (mapping happens before a key is even
chosen — see §3.2) to get three lists: `common_fields` (rendered as
`auto_matched_fields` — fields that already share a name on both sides,
needing no action), `only_in_soap`, `only_in_rest`.

Client-side, the page has two sections:

- **Auto-matched fields** — read-only rows, one per already-common field,
  shown purely for visibility (not editable — there's nothing to choose).
- **Unmatched fields** — one row per SOAP-only field, each with a
  `<select>` of REST-only field names to pair it with (or "no match").
  **Mutual exclusion**: once a REST-only field is chosen by one row, it's
  removed from every other row's dropdown options (same pattern reused for
  the Select Matching Key page's multi-key picker) — implemented by
  rebuilding every `<select>`'s option list on every `change` event.

On submit, the JS serializes only the rows that actually have a pairing
chosen into a flat `{soap_field: rest_field}` object, POSTed as
`mapping_json` — this becomes `column_mapping` in `PENDING`, later applied
by `engine.apply_column_mapping` (§4).

### 4.4 Drop Fields — page mechanics

Simpler: a classic **dual-listbox** (`drop_fields.html`) — "Available
Fields" on the left, "Fields to Drop" on the right, with `>`/`<`/`>>`/`<<`
move buttons between them. Submitting serializes the right-hand list's
field names into `dropped_fields_json` → becomes `ignored_fields` in
`PENDING`, which `diff_columns`'s `ignored_fields` parameter excludes from
every downstream comparison, report, and the negative-case scan alike.

---

## 5. Advanced Options (on the Upload page)

Three subsections, per tab (Field Comparison / Negative Case Testing —
the third, Field Comparison Logic, is compare-only, see below):

1. **Managed Package Namespaces** — `soap_namespace`/`rest_namespace`
   text inputs, defaulting to `breadwinner_ns` / `rc_breadwinner`. Skip
   this if your sheets already use plain Field Labels instead of API
   names.
2. **Field Metadata / Evidence Count** *(compare tab only)* —
   `full_by_default` checkbox (checked by default — every field scans
   FULL) and a `default_n` number input, used only when `full_by_default`
   is unchecked. **The old "Field Metadata Sheet" third-file-upload
   feature was removed entirely** — testing size is now a single global
   setting, not per-field (per-field *comparison logic* is now handled by
   the Field Comparison Logic wizard step instead, see §6).
3. **Wizard Steps** — the four "Always show X" checkboxes from §3.1 (Map
   and Key default unchecked, Drop and Field Comparison Logic default
   checked). The negative tab only has three (no Field
   Comparison Logic checkbox — see §7).

---

## 6. Comparison Logic system

### 6.1 Why this exists

Originally every field was hardcoded to a strict, case-sensitive,
byte-for-byte string comparison. That produces a lot of false-positive
noise: a date field that's genuinely correct but formatted `MM/DD/YYYY` on
one side and `YYYY-MM-DD` on the other would show as a scary "Mismatch"
even though the underlying data round-tripped correctly. This system
exists to let QA tell the tool *which* fields need smarter handling,
without writing code — just picking from a dropdown on the **Field
Comparison Logic** wizard page.

### 6.2 The five manually-assignable logic types (+ one automatic one)

| Logic (`field_logic` value) | UI label | Applies to |
|---|---|---|
| *(empty string / unassigned)* | "Case-insensitive (default)" | every field, unless explicitly overridden |
| `STRICT` | "Strict (case-sensitive)" | opt-in only — for fields where exact casing genuinely matters |
| `DATE` | "Date/DateTime" | date or datetime fields |
| `PICKLIST` | "Picklist (order-insensitive)" | multi-value fields where value order doesn't matter |
| `NUMBER` | "Number" | numeric fields |

`BOOLEAN` also exists (see §6.4a) but is **not** in this dropdown — it's
applied automatically, not manually assigned. See §6.4a for why, and for
what "automatically" means precisely (it overrides *any* manual choice,
including one of the five above).

**Case-insensitive is the default for every unassigned field** — this was
an explicit, deliberate decision (see conversation history: "I want this
to be default"). `STRICT` still exists as an opt-in for the rare field
where casing must be exact. If you're used to thinking "unassigned =
strict," that's wrong for this codebase — unassigned = case-insensitive.

### 6.3 Dispatch order (`_classify` in `comparison_engine.py`)

This exact order matters — read it before changing anything:

```python
def _classify(soap_val, rest_val, logic=DEFAULT_LOGIC):
    # 1. Blank asymmetry always wins, regardless of logic:
    if soap_blank and not rest_blank: return "Mismatch", "SOAP is blank, REST has a value"
    if rest_blank and not soap_blank: return "Mismatch", "REST is blank, SOAP has a value"
    # 2. Exact string match always wins, regardless of logic -- this runs
    #    BEFORE any date/number parsing, so identical strings are always
    #    the cheapest, most unambiguous path to Verified:
    if soap_val == rest_val: return "Verified", ""
    # 3. Only reached when the raw strings differ and neither is blank --
    #    dispatch to the assigned logic. Each of DATE/PICKLIST/NUMBER/
    #    STRICT is EXCLUSIVE -- only its own check ever runs, no fallback
    #    to any other logic's leniency:
    if logic == LOGIC_DATE: return _classify_date(...)
    if logic == LOGIC_PICKLIST: return _classify_picklist(...)
    if logic == LOGIC_NUMBER: return _classify_number(...)
    if logic == LOGIC_STRICT: return "Mismatch", "Values differ"   # <- must be explicit, see below
    if logic == LOGIC_BOOLEAN: return _classify_case_insensitive(...)  # casing only, no benefit of the doubt beyond that
    return _classify_default(...)   # true default/unassigned fallback -- see below
```

**Why the `STRICT` branch has to be explicit, not implicit:** without it,
a `STRICT` field would fall through to the final `_classify_default`/
`_classify_case_insensitive` call and silently get leniency anyway —
completely defeating the point of offering `STRICT` as a distinct opt-in.
The explicit `if logic == LOGIC_STRICT: return "Mismatch", ...` line is
what *intercepts* a strict field before it ever reaches any fallback.

**`_classify_default` — the true default/unassigned path gets three
layers of benefit of the doubt, not just one.** A field left unassigned
(the vast majority of fields, in practice) tries, in order: (1)
case-insensitive equality — same value, different casing; (2)
Picklist-style order-insensitive equality — same values, different order
(e.g. `"A;B"` vs `"B;A"`); (3) Number-style formatting-insensitive
equality — same number, different precision/formatting (e.g. `"2.50"` vs
`"2.5"`). Each sub-check's own `Mismatch` fallback text is discarded here
— only an `Accepted-Mismatch` result from any of the three is taken; if
all three fail to explain the difference, it's a genuine `Mismatch`,
`"Values differ"`.

**This benefit-of-the-doubt chain is deliberately scoped to the default
path only** — an explicit assignment is exclusive (§ above: `DATE` only
ever tries `_classify_date`, never picklist/number leniency too, even if
its own date-parsing fails and falls back to `"Values differ"`), and
`BOOLEAN` gets only casing leniency, never order/number — a real
`True`/`False` disagreement must always stay a hard `Mismatch`, no benefit
of the doubt, since order/number checks make no sense for a two-value
field and could only ever mask a genuine boolean disagreement.

**Standing rule for any future comparison logic added to this system:**
when adding a new logic type, either (a) add it into `_classify_default`'s
benefit-of-the-doubt chain too (if it's the kind of check that plausibly
applies to a generic, unassigned field — like Picklist and Number are),
or (b) explicitly ask the user whether it belongs there, rather than
silently deciding either way. Don't assume a new logic is exclusive-only
(like `DATE`/`STRICT`) or stacked-into-default (like `PICKLIST`/`NUMBER`)
without checking — this was a deliberate, explicit decision each time
(see conversation history), not an obvious default either way.

**`Verified` can only ever come from step 2** (exact string match). None
of `_classify_date` / `_classify_picklist` / `_classify_number` /
`_classify_case_insensitive` / `_classify_default` ever return
`"Verified"` — they're only reached because step 2 already failed, so by
construction there's nothing left for them to verify. They only ever
return `Accepted-Mismatch` or `Mismatch`.

### 6.4 `Accepted-Mismatch` — the fourth Tested status

A genuinely new status, added specifically for this logic system: **the
underlying data is the same, but the raw strings differ in a benign,
explainable way.** Not `Verified` (the strings did differ), not a scary
`Mismatch` (there's no real data problem) — its own middle ground.

Per-logic rules for what counts as "benign":

- **Date** (`_classify_date`): parses both values with `pd.to_datetime`.
  - Either side fails to parse at all → falls back to plain `Mismatch`,
    `"Values differ"` (same fallback pattern as every other logic).
  - Parsed timestamps are exactly equal → `Accepted-Mismatch`, `"Same
    date and time — only the format differs"`.
  - Otherwise, compares `.date()` and `.time()` parts independently to
    give a precise reason: `"Date differs, time matches"` /
    `"Time differs, date matches"` / `"Values differ"` (both differ).
  - **Works transparently for pure date-only fields too** — with no time
    component, both sides default to midnight, so the time comparison is
    trivially equal and only the date matters. One code, no separate
    "DateTime" logic needed (verified explicitly, see history).
- **Picklist** (`_classify_picklist`): splits both values on `;` or `,`,
  strips whitespace, drops empty entries, sorts, compares as sets.
  Same set → `Accepted-Mismatch`, `"Same values, different order"`.
  Different set → `Mismatch`, `"Values differ"`.
- **Number** (`_classify_number`): strips whitespace and thousands-comma
  separators, parses as `float`. Either side unparseable → `Mismatch`,
  `"Values differ"`. Same numeric value (handles leading/trailing zeros,
  decimal precision / integer-vs-double, thousands separators, stray
  whitespace) → `Accepted-Mismatch`, `"Same number, only formatting
  differs"`. Genuinely different value → `Mismatch`, `"Values differ"`.
- **Case-insensitive** (`_classify_case_insensitive`, the default):
  `.lower()` both sides and compare. Same → `Accepted-Mismatch`, `"Same
  value, different casing"`. Different → `Mismatch`, `"Values differ"`.

### 6.4a `BOOLEAN` logic — auto-detected, False treated as blank

**Not manually assignable — auto-detected from the data, and always
wins.** `build_reports` computes, per field, `_is_boolean_only_field(
soap_by_id, rest_by_id)`: true if every non-blank value for that field, on
*both* sides, is exactly `"true"` or `"false"` (case-insensitive) — an
all-blank field doesn't count, since there's nothing to detect. If true,
`logic` is forced to `LOGIC_BOOLEAN` for that field **regardless of
`field_logic`** — even overriding an explicit manual assignment (e.g.
`STRICT`) the user made for that field in the Field Comparison Logic
wizard step. There is deliberately no dropdown entry for it (§6.2) —
picking anything for a genuinely true/false-only field has no effect,
since auto-detection overrides it anyway, so offering the choice would
just be misleading.

Why auto-detect instead of a manual opt-in: a boolean field is
unambiguously identifiable from its actual values (unlike, say, telling a
date field from a plain number, which genuinely needs a human's judgment)
— there's no scenario where a field that's 100% true/false-valued
*shouldn't* get this treatment, so making the user configure it added a
step with no real decision in it.

Unlike the other five logics, `BOOLEAN` doesn't hook into `_classify`'s
dispatch at all (a False/False or True/True pair is never *unequal*, so
it never even reaches step 3's dispatch — see §6.3). Instead it hooks into
the **blank-equivalence check** that both `_compare_field_sample` and
`_compare_field_full` run before calling `_classify`:
`_is_blank_equivalent(soap_val, rest_val, logic)` returns `True` for a
real blank-blank pair (as always), **or**, when `logic == LOGIC_BOOLEAN`,
for a `False`/`False` pair (case-insensitive, via `_is_boolean_false`).

Rationale: a boolean field's `False` is usually just NetSuite/Salesforce's
default/unset state — as uninformative as a blank cell, not a positive
confirmation that both systems computed the same real value. So:

- **N-sample mode**: a `False`/`False` pair is skipped entirely, exactly
  like blank-blank — it never consumes one of the N sample slots.
- **FULL mode**: a `False`/`False` pair is reported as `Unverified`, not
  `Verified` — same distinct-third-outcome treatment as blank-blank.
- **`True`/`True`** is completely unaffected — still a normal `Verified`
  match (via `_classify`'s step-2 exact-match check, same as any other
  logic). One side `True`, other `False` → still a normal `Mismatch`, also
  unaffected (only a matching-False pair gets the blank treatment; a
  False that disagrees with a True is not "uninformative").

This was added after real QA data showed the opposite problem this logic
now avoids: with `False` treated as a normal value, if `False`/`False`
records happened to scan before any `True`/`True` records, the condensed
report's Verified examples (see §8.1) could show 3 `False`/`False` rows
and never surface that `True`/`True` matches existed too — worse, it also
meant `False`/`False` (which proves nothing) was diluting/hiding genuine
`Mismatch`/`Unverified` signal for that field.

### 6.5 Field-level status rollup priority

A field's overall dashboard status (used for the Results page stat cards)
is computed once per field from all its evidence rows, in this priority
order — **higher priority wins if any row has it, regardless of how many
rows have a lower-priority status**:

```
Mismatch > Accepted-Mismatch > Verified > Unverified
```

i.e.: any real `Mismatch` anywhere → field is "Failed", full stop, even if
9 other records were fine. No `Mismatch` but at least one
`Accepted-Mismatch` → field goes in the "Accepted-Mismatch" bucket, even if
some records were plain `Verified` too (the idea: even one benign
difference is worth a human glance, not silently folded into a clean
"Passed"). No `Mismatch`/`Accepted-Mismatch` but at least one `Verified` →
"Passed". All `Unverified` (every matched record was blank-blank) →
"Unverified".

Dashboard stat cards on Results (`results.html`): **Fields Passed**,
**Fields Failed**, **Fields with Accepted Differences**, **Fields
Unverified**, plus **Missing records** (a record present in one export
but not the other — computed separately, not part of this priority
scheme).

### 6.6 Auto-suggestion: "Date/DateTime" for fields with "date" or "time" in the name

`_render_field_logic` in `app.py` computes `auto_suggested_fields = [f for
f in available_fields if ("date" in f.lower() or "time" in f.lower()) and
f not in field_logic]` — any field whose name contains "date" **or**
"time" (case-insensitive substring, e.g. `Created Date`, `Last Modified
Date`, `Start Time__c`) gets `DATE` pre-selected in its dropdown (UI label
"Date/DateTime"), tagged with a small purple **"Auto Selected"** ribbon
(`.pill-mini.pill-mini-accent` in `style.css`). This is purely a starting
suggestion, not a lock: the dropdown is fully editable, and the tag
disappears the moment the row's `<select>` fires a `change` event (see the
JS in `field_logic.html`) — once the user touches it, it's their explicit
choice either way. Note the condition `f not in field_logic`: an
already-confirmed choice for that field (from a previous visit /
Back-then-forward) is never overridden by the auto-suggestion.

**Known over-trigger, accepted as a tradeoff:** adding "time" catches a
field like `Time Zone__c` too, even though a timezone identifier isn't a
date/time *value* to parse — there's no cheap name-based way to tell
"Start Time" from "Time Zone" apart. Not fixed, because it's just a
pre-selected suggestion the user can (and should, for a field like that)
override — broadening the heuristic to actually catch more real date/time
fields was judged worth the occasional false positive.

### 6.7 `QA_NOTE_CLASSIFICATION_GUIDE.md`

A separate reference file (not superseded by this one) — a lookup catalog
for turning a raw, freeform QA-notes sheet (`Field Name` + plain-English
note) into actual `field_logic` assignments. Written for any agent or
human to use without re-deriving comparison behavior from scratch each
time a new object's QA notes show up. Keep it in sync if you add new logic
types.

### 6.8 Deferred / shelved comparison-logic ideas

Do not build these unless the user explicitly asks again — they were
discussed at length and deliberately not pursued:

- **`Conditional` logic** (a field's expected value depends on another
  field's value, e.g. `First Name` should only be populated when `Type =
  Person`). Fully designed (condition field + condition value + a
  three-option "Has any value / Is blank / Equals specific value"
  expected-behavior control, comment templates for every outcome) but
  explicitly shelved by the user ("shelve the conditional for now") —
  nothing was implemented. If revisited, the full design is preserved in
  conversation history, not in this file (it was never built, so there's
  no code to document).
- **`Pattern` logic** (regex-match, e.g. proving leading zeros survive a
  round-trip) — mentioned early on, never designed in detail, status
  explicitly left "undecided."
- **`Link` logic** (making a lookup field's value a clickable hyperlink to
  the actual Salesforce/NetSuite record in the exported Excel) — raised
  once, explicitly dropped by the user ("this is not needed") after
  clarifying it would need the actual record ID (not just display Name)
  and a known URL pattern per system, neither of which the exports
  currently provide.

---

## 7. Two flows: Field Comparison vs Negative Case Testing

Selected via tabs on the Upload page (`index.html`), each with its own
form and its own set of Advanced Options.

**Field Comparison** (`flow: "compare"`) is the main flow described
throughout this document — full wizard, `build_reports`, 4 downloadable
reports (§8).

**Negative Case Testing** (`flow: "negative"`) is a narrower, exhaustive
scan: for every matched record and every common field, report every case
where **SOAP has a value but REST is blank** (`build_negative_case_report`
— checks only this one direction, never the reverse, never sampled or
capped). Shares Map Unmatched Fields / Select Matching Key / Drop Fields
with the compare flow, but:

- **Never shows Field Comparison Logic**, regardless of any checkbox —
  `build_negative_case_report` has its own hardcoded blank/not-blank
  check and never consults `field_logic` at all, so assigning Date/
  Picklist/Number logic there would be a pure no-op. `_advance_after_drop`
  and `_retreat_to_field_logic_or_earlier` both gate on `pending["flow"]
  == "compare"` before even considering the step. There is deliberately no
  `neg_always_show_field_logic` checkbox in the negative tab's Advanced
  Options — don't add one back without also wiring real behavior for it.
- Produces one report pair (`negative_csv`/`negative_xlsx`), not four.
- Every row is `Tested = "Mismatch"`, comment `"REST is blank, SOAP has a
  value (negative case)"` — no condensing, no logic dispatch, one row per
  actual occurrence (`flat_rows_to_dataframe`).

---

## 8. Reports produced (Field Comparison flow)

All four are generated together by `_run_compare` → written to a
per-request temp dir → served via `/download/<request_id>/<which>`.

**Column order** (`COMPARISON_COLUMNS` / `MISMATCHED_RECORDS_COLUMNS` in
`comparison_engine.py`): `Field Label, Evidence, Soap Value, Rest Value,
[Tested,] Comments` — `Evidence` comes right after `Field Label`, before
the two values, on every report that has all three columns (Field
Comparison, Mismatched Records, and the flat Negative Case report). This
is an explicit ordering choice, not incidental — changing it means editing
both column-order constants, not just one, since they're independent
lists (not derived from each other).

1. **Field Comparison Report** (`comparison_csv`/`comparison_xlsx`) — the
   main condensed report. See §8.1 for exactly how condensing works.
2. **Missing Records Report** (`missing_csv`/`missing_xlsx`) — one row per
   record key present in only one side (`Record Key`, `Missing In`
   [`"SOAP"`/`"REST"`], `Comments`).
3. **Detailed Mismatched Records Report**
   (`mismatched_csv`/`mismatched_xlsx`) — **exhaustive, uncapped** list of
   every real `Mismatch` (via `mismatched_rows_to_dataframe`, which
   filters on the literal string `"Mismatch"` — `Accepted-Mismatch` rows
   are automatically excluded, no special-casing needed since they're a
   different string). Ends with a per-field tally
   (`mismatch_summary_lines`: `"Field -> N mismatched"`), same
   `"Mismatch"`-only filter, so `Accepted-Mismatch` never pollutes the
   tally either.
4. Excel versions (`write_comparison_excel`) merge/center the `Field
   Label` column across each field's consecutive rows, and wrap-text +
   auto-height every cell containing embedded `\n`-joined multi-line
   content.

### 8.1 Condensing (`condense_rows` in `comparison_engine.py`)

**This is purely a display transformation — it never changes the
underlying scan.** The full, flat `EvidenceRow` list (one row per
record/field pair actually compared) is grouped by field, then each
field's rows are bucketed by `Tested` status and packed down to at most 4
condensed rows per field:

| Tested | Cap constant | Value | Diversified? |
|---|---|---|---|
| `Verified` | `MAX_VERIFIED_EXAMPLES` | 3 | **Yes — by value** |
| `Mismatch` | `MAX_MISMATCH_EXAMPLES` | 5 | **Yes — by reason** |
| `Accepted-Mismatch` | `MAX_ACCEPTED_MISMATCH_EXAMPLES` | 5 | **Yes — by reason** |
| `Unverified` | `MAX_UNVERIFIED_EXAMPLES` | 3 | No — first N in scan order |

**Diversified packing** (`_pack_diverse_examples(bucket, cap, key,
allow_exceed_cap)`): instead of blindly taking the first N examples in
scan order (which could all coincidentally be the same underlying
value/reason), it groups by `key(row)` and takes one example per distinct
key first, then, only if there's still room, pads up to the cap with more
examples of whatever key.

- **Mismatch / Accepted-Mismatch** key off the row's exact `.comments`
  string (the reason), with `allow_exceed_cap=True` (the default) —
  **exceeding the cap if there are more distinct reasons than it allows**.
  Safe here because a field only ever has a handful of possible *reason*
  strings (e.g. "Date differs, time matches" vs "Values differ"), so a
  Date field with both will show at least one of each, not 5
  near-duplicates of whichever happened to scan first.
- **Verified** keys off `.soap_value` instead, with `allow_exceed_cap=False`
  — an exact match has no per-record reason (`.comments` is blank for all
  of them), so diversifying by reason would do nothing; diversifying by
  value instead surfaces a boolean field's `True`/`True` matches even when
  `False`/`False` happens to scan first. **`allow_exceed_cap` must be
  `False` here**, unlike Mismatch/Accepted-Mismatch: a *value* (unlike a
  reason) can have unbounded cardinality — a numeric or free-text field can
  have a different value on nearly every row. Trying "one example per
  distinct value, exceed the cap if needed" on Verified was tried and
  immediately broke in production: a field with 47 verified records, all
  different numbers, dumped all 47 as "diverse examples" instead of the
  intended 3. `allow_exceed_cap=False` still prefers one example per
  distinct value first, but truncates to `cap` no matter how many distinct
  values exist.

Why this matters concretely: Date/Picklist/Number/Case-insensitive logic
introduced *genuinely different reasons* a field can mismatch (unlike the
old plain-STRICT world where "Values differ" was the only reason that
ever existed) — diversified packing is what makes those different reasons
actually visible in the condensed report instead of getting randomly
buried.

**Comments column, per bucket:**

- **`Verified` and `Unverified` both show one shared scan-summary line**,
  not a per-example reason: `"Out of N compared, X verified, Y mismatched,
  Z accepted-mismatch, and W unverified (both blank)."` — or `"(both
  False)"` if every `Unverified` row in the group is a `BOOLEAN`-logic
  False-False pair (§6.4a), or `"(both blank or both False)"` if the group
  has a mix of both reasons. Grouped together deliberately: neither bucket
  has a per-record reason worth repeating — an exact match needs no
  explanation, and "blank-blank" / "False-False" is definitionally the
  only possible reason a record lands in `Unverified` at all (there's no
  second scenario it could be), so showing the same one-line reason N
  times across N packed examples added nothing. Per-record `EvidenceRow`
  comments (`"Both blank"` / `"Both False"`, set in `_compare_field_full`
  right where `_is_blank_equivalent` fires) still exist and still drive
  the summary's own `(both blank)`/`(both False)` wording — they're just
  not surfaced per-example in the condensed report anymore.
- **`Mismatch` / `Accepted-Mismatch` carry the real, specific per-record
  reason** (e.g. `"Same value, different casing"`, `"Date differs, time
  matches"`) — this was a deliberate fix (previously *all* buckets shared
  the one generic summary line, which for `Accepted-Mismatch` rows meant
  the actual "why" was never shown anywhere in the condensed report — a
  real bug, since fixed). Each packed example's own comment already
  includes its FULL-mode context suffix (`"(full scan: N records, M
  mismatches)"`) baked in from `_compare_field_full`.
  - **But if every packed example shares the exact same reason string**,
    it's shown **once**, not repeated per example — same "don't repeat an
    identical line N times" principle as Verified/Unverified. Only
    genuinely *distinct* reasons among the packed examples (e.g. one
    example says "Date differs, time matches" and another says "Time
    differs, date matches") get their own separate comment line. Computed
    per-field from `{r.comments for r in sample}` — one distinct value
    means one comment; more than one means the full per-example list.
  - **The overall per-field summary line always appears somewhere.** It's
    guaranteed on `Verified`/`Unverified` whenever either exists (see
    above). A field with *neither* (only `Mismatch` and/or
    `Accepted-Mismatch` rows) would otherwise never show it at all — so in
    that case it's prepended as an extra leading comment line onto
    whichever of `Mismatch`/`Accepted-Mismatch` is emitted first
    (`Mismatch` takes priority if both exist), tracked via
    `summary_needs_injection`/`injected_summary` in `condense_rows`.

### 8.2 N-sample vs FULL mode, and blank-blank handling

Two different scan strategies per field, chosen via `testing_size`
(currently: one global setting, `full_by_default` checkbox + `default_n`
number input — **not** per-field anymore, see §5):

- **N-sample mode** (`_compare_field_sample`): walks matched records in
  sheet order, **skips a record entirely if both sides are blank** (no
  evidence row at all — doesn't count toward anything), and stops as soon
  as `n` evidence rows have been collected. Mismatches and Verifieds both
  count toward that `n` cap equally (this was an explicit early design
  decision — "Option A").
- **FULL mode** (`_compare_field_full`): scans **every** matched record,
  never stops early, and — unlike sample mode — **does not skip
  blank-blank pairs**. Instead it reports them as their own row with
  `Tested = "Unverified"` — a third outcome distinct from Verified/
  Mismatch, since neither side actually had a value to prove anything
  either way. Every row (regardless of outcome) gets a `"(full scan: N
  records, M mismatches)"` suffix appended to its comment, where `M` only
  ever counts real `Mismatch` rows.

This is why `Unverified` exists as a status at all: it's **only ever
produced in FULL mode**, specifically for the both-sides-blank case. In
N-sample mode, a both-blank pair simply never generates a row.

---

## 9. Verification approach (no automated tests)

There is no test suite in this repo. Every feature and bug fix in this
project's history was verified by:

1. Restarting the Flask dev server (`lsof -ti:5000 | xargs kill -9 2>/dev/null; ... nohup python app.py > /tmp/flask.log 2>&1 &`).
2. Driving the actual HTTP routes with `curl -F "field=value" ...`,
   chaining `pending_id` through each step exactly as the browser would,
   asserting on response `<title>` tags and specific rendered values
   (grep on the raw HTML/CSV output).
3. For pure-engine logic changes, a quick standalone `python3 -c` /
   heredoc script importing `comparison_engine` directly and asserting on
   `_classify_*` return values before wiring anything into the app.

**If you add new behavior, verify it this same way** — end-to-end via
curl through the real routes, not just unit-testing the engine function
in isolation, since most bugs in this project's history were in the
*wiring* (which route calls which advance/retreat function, whether a
hidden form field's name matches what the route reads) rather than in the
comparison logic itself. A recurring specific gotcha worth remembering:
**never write a real `{% include %}` or `{{ }}` inside an HTML comment in
a template** — Jinja renders inside HTML comments too, so a comment
describing "you could include X here" can accidentally cause infinite
mutual recursion between two templates (this happened once — see history
— caused a `RecursionError` crash).

---

## 10. Frontend notes

- **No shared base template.** Each `templates/*.html` file is a complete
  standalone page (no `<html>`/`<head>`/`<body>` tags even — browsers
  auto-wrap loose HTML5, and Flask's `render_template` doesn't inject any
  wrapper). Shared bits are Jinja partials, `{% include %}`'d
  individually into every page that needs them:
  - `_theme_toggle.html` — the light/dark toggle button + its inline
    theme-persistence script + the `<link rel="icon">` favicon tag.
    Included at the top of every page.
  - `_start_button_cinematic.html` / `_start_button_classic.html` — see
    below.
- **Theming**: CSS custom properties in `style.css`, default light theme
  on bare `:root`, dark theme both via `@media (prefers-color-scheme:
  dark)` (guarded `:root:not([data-theme="light"])`) and via an explicit
  `[data-theme="dark"]`/`[data-theme="light"]` override that the toggle
  button sets via `localStorage` + a `data-theme` attribute on
  `<html>`. Palette: warm rust/orange "Prometheus" theme (`--accent:
  #e64833` light / `#ef6a52` dark, cream/sand light background, deep
  slate-blue dark background) — chosen deliberately to match the
  car-engine-ignition motif of the Start button, replacing an earlier
  cool purple/black scheme.
- **Cache-busting**: every stylesheet/script link is suffixed
  `?v={{ asset_version }}` (or `asset_version_for('path')` for the
  isolated animation files), a Flask `context_processor` that returns each
  static file's mtime as an integer. Without this, browsers would
  aggressively cache `style.css` across edits during development.
- **Cinematic Start button** (`static/css/engine-animation.css`,
  `static/js/engine-animation.js`, `templates/_start_button_cinematic.html`):
  a from-scratch inline-SVG scene (mechanic figure + engine with flywheel/
  exhaust/bolts) with a multi-phase click animation (pull rope → engine
  shake → flywheel spin + rising smoke → "✓ Engine Running"), plus a
  "Motorius Ignitio!" comic-book-style caption that pops in during the
  pull. Deliberately kept in its own isolated CSS/JS files (not merged
  into `style.css`) specifically so it can be swapped out instantly: the
  original plain circular button is preserved verbatim in
  `_start_button_classic.html`, and `options.html` includes whichever one
  is currently wired in with a single `{% include %}` line — **to revert,
  change that one line, nothing else.**
  - **Timing model, chosen deliberately after iterating through worse
    options**: the button does *not* call `preventDefault()`. The native
    form submission fires immediately on click, exactly like every other
    button in the app, so the browser's tab-loading spinner is real and
    honest from the first click. The animation's `setTimeout`-driven CSS
    class changes are purely cosmetic, riding along the real page load —
    they get cut off naturally whenever the real response arrives and the
    next page swaps in, with no fake artificial delay gating the actual
    request. (An earlier `fetch()`-based version fired the request
    immediately in the background so the *full* animation always played
    to completion, but was reverted because `fetch()` never triggers the
    browser's tab-loading spinner at all, which read as less honest than
    a real navigation with a possibly-shorter animation.)
  - A well-known SVG+CSS gotcha bitten repeatedly during development,
    worth remembering: if an element has an SVG presentation attribute
    `transform="translate(...)"` **and** a CSS rule that also sets
    `transform: ...`, the CSS value silently and completely replaces the
    attribute — never partially merges with it. The fix used throughout:
    an **outer `<g>`** holds the static positioning `transform` attribute,
    and an **inner nested `<g>`** receives *only* CSS-driven `transform`
    (e.g. `rotate()` for the arm, `scale()` for the pop-in spell text).
- **SVG-shape-language lesson** (from the pull-rope redesign iterations):
  a thick line ending in a circle reads as a mallet/hammer silhouette at
  small icon scale, no matter how it's angled — that's a shape-language
  problem, not a geometry-tuning one. The final rope is a fixed-length
  straight bar, no stretch animation, attached to the fist the whole
  time — simplicity won out over literal "bend down and pick up the rope"
  realism, which didn't read well at this scale.

### 10.1 Reusable CSS/UI conventions — reuse these, don't reinvent

These patterns are already established in `style.css` and used across
multiple wizard pages. When adding a new page or control, check here
first instead of inventing a new pattern from scratch (this happened more
than once during development and had to be reconciled after the fact):

- **`.pill`** — the object-name ribbon badge (top-right of every card,
  e.g. "Customer"). Notched banner shape via `clip-path: polygon(...)`,
  with folded-corner triangles via `::before`/`::after`. **Uses `filter:
  drop-shadow(...)`, not `box-shadow`** — a plain `box-shadow` would
  render as a rectangle and ignore the clip-path entirely; `drop-shadow`
  respects the clipped silhouette.
- **`.pill-mini`** — a smaller version of the same ribbon technique, used
  inline within a row (e.g. the green "Auto-Matched" tag on Map Unmatched
  Fields, the purple "Auto Selected" tag on Field Comparison Logic).
  `.pill-mini-accent` is the accent-colored (purple) variant; the base
  `.pill-mini` is green (`var(--success)`).
- **`.advanced-toggle` / `.advanced-fields`** — the collapsible-section
  pattern (chevron icon that rotates on open, content that's `display:
  none` until toggled). Used for "Advanced options" on Upload and the
  "Mapped Fields" collapsed section on Map Unmatched Fields. Pair a
  toggle's `id="advanced-toggle-X"` with content's `id="advanced-fields-X"`
  — the generic JS in `index.html` derives one id from the other by
  string-replacing the prefix, so keep that naming convention for any new
  collapsible section.
- **`.section-heading`** — shared bold/accent-colored heading style, used
  both for plain section titles (e.g. "Unmatched Fields") and applied
  directly to `.advanced-toggle` buttons so a clickable collapsible
  heading looks visually identical to a plain static one.
- **`.field-map-row` / `.field-map-header`** — the two-column grid layout
  (label + control) shared by Map Unmatched Fields' pairing rows and
  Field Comparison Logic's per-field dropdown rows. Reuse this instead of
  a new grid for any future "one row per field, with a control" page.
- **`.dual-listbox`** — the two-listbox-with-move-buttons picker, used by
  Drop Fields (Available/Dropped) and originally by the old ignore-fields
  modal it replaced.
- **`.options-actions.secondary-row.centered`** — the compact,
  centered Back/Next/Start-Over button row at the bottom of every wizard
  page. Keep new pages' navigation buttons in this same class combination
  for visual consistency.
- **Theme-aware color tokens only** — never hardcode a color; every
  visual element uses a `var(--...)` custom property defined once in
  `style.css`'s `:root` (see §10 above) so it stays correct across light/
  dark and any future palette change.

---

## 11. Backend routes (full list)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Upload page |
| `/compare` | POST | Start compare flow, `_advance_after_upload` |
| `/negative-test` | POST | Start negative flow, `_advance_after_upload` |
| `/confirm-mapping` | POST | Save column mapping, `_advance_after_mapping` |
| `/back-to-mapping` | POST | `_retreat_to_mapping_or_earlier` |
| `/confirm-key` | POST | Save key columns, `_advance_after_key` |
| `/back-to-key` | POST | `_retreat_to_key_or_earlier` |
| `/confirm-drop` | POST | Save dropped fields, `_advance_after_drop` |
| `/back-to-drop` | POST | `_retreat_to_drop_or_earlier` |
| `/confirm-field-logic` | POST | Save field logic assignments, → `_render_options` |
| `/back-to-field-logic` | POST | `_retreat_to_field_logic_or_earlier` |
| `/back-to-upload` | POST | `_render_upload_with_prefill` |
| `/back-to-options` | POST | Results page's Back → `_render_options` directly |
| `/run` | POST | Final step: loads+normalizes data, dispatches to `_run_compare` or `_run_negative` |
| `/download/<request_id>/<which>` | GET | Serves a generated report file |

`which` for compare-flow downloads:
`comparison_csv`/`comparison_xlsx`, `missing_csv`/`missing_xlsx`,
`mismatched_csv`/`mismatched_xlsx`. For negative-flow:
`negative_csv`/`negative_xlsx`.

---

## 12. Hosting caveats (if this ever gets deployed beyond one local user)

- `PENDING`/`RUNS` are plain in-process dicts — **will not work correctly
  behind multiple worker processes** (e.g. gunicorn with `workers > 1`),
  since each worker has its own memory. Would need a shared store (Redis,
  a DB table) before that's viable.
- Nothing ever expires or gets cleaned up — uploaded files, generated
  reports, and pending sessions accumulate in `tempfile.mkdtemp()`
  directories and the in-memory dicts forever, for the lifetime of the
  process.
- `app.run(debug=True)` — the Flask dev server, explicitly not meant for
  production (Werkzeug prints this warning itself on every start).

---

## 13. Quick orientation for a new agent

1. Read this file fully before touching code.
2. `comparison_engine.py` has zero Flask dependency — you can iterate on
   comparison logic with a plain `python3` script importing it directly
   (fast feedback loop, see §9).
3. `app.py`'s `_advance_after_*` / `_retreat_to_*_or_earlier` functions are
   the wizard's entire control flow — read them together as one unit
   before changing any single route.
4. Every template is standalone; there's no layout inheritance to trace.
5. When adding a new wizard step, you'll touch: a new `_render_*` helper
   and `_advance_after_*`/`_retreat_to_*_or_earlier` pair in `app.py`, a
   new `always_show_*` checkbox in `index.html` (both tabs, unless it's
   compare-only), a new template, and this file.
6. When adding a new Comparison Logic type, you'll touch: a new
   `_classify_*` function + a new `LOGIC_*` constant + a new dispatch
   branch in `_classify` (comparison_engine.py), a new `<option>` in
   `field_logic.html`'s `LOGIC_OPTIONS`, and — if it should ever produce
   `Accepted-Mismatch` — nothing else, that status is already fully
   wired through condensing, the dashboard, and report exclusion.
