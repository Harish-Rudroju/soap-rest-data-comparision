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

| Step | Checkbox (default: checked) | Auto-shows even if unchecked, when... |
|---|---|---|
| Map Unmatched Fields | `always_show_mapping` | some SOAP/REST columns don't share a name (`needs_mapping`) |
| Select Matching Key | `always_show_key` | no `Internal Id`-like column found in both sheets (`has_default_key` is false) |
| Drop Fields | `always_show_drop` | never — pure user choice, no way to detect "need" |
| Field Comparison Logic | `always_show_field_logic` | never — pure user choice; **compare-flow only**, see §7 |

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
3. **Wizard Steps** — the four "Always show X" checkboxes from §3.1,
   all checked by default. The negative tab only has three (no Field
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

### 6.2 The five logic types

| Logic (`field_logic` value) | UI label | Applies to |
|---|---|---|
| *(empty string / unassigned)* | "Case-insensitive (default)" | every field, unless explicitly overridden |
| `STRICT` | "Strict (case-sensitive)" | opt-in only — for fields where exact casing genuinely matters |
| `DATE` | "Date" | date or datetime fields |
| `PICKLIST` | "Picklist (order-insensitive)" | multi-value fields where value order doesn't matter |
| `NUMBER` | "Number" | numeric fields |

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
    #    dispatch to the assigned logic:
    if logic == LOGIC_DATE: return _classify_date(...)
    if logic == LOGIC_PICKLIST: return _classify_picklist(...)
    if logic == LOGIC_NUMBER: return _classify_number(...)
    if logic == LOGIC_STRICT: return "Mismatch", "Values differ"   # <- must be explicit, see below
    return _classify_case_insensitive(...)   # default fallback
```

**Why the `STRICT` branch has to be explicit, not implicit:** without it,
a `STRICT` field would fall through to the final `_classify_case_insensitive`
call and silently get case-insensitive treatment anyway — completely
defeating the point of offering `STRICT` as a distinct opt-in. The
explicit `if logic == LOGIC_STRICT: return "Mismatch", ...` line is what
*intercepts* a strict field before it ever reaches the default fallback.

**`Verified` can only ever come from step 2** (exact string match). None
of `_classify_date` / `_classify_picklist` / `_classify_number` /
`_classify_case_insensitive` ever return `"Verified"` — they're only
reached because step 2 already failed, so by construction there's nothing
left for them to verify. They only ever return `Accepted-Mismatch` or
`Mismatch`.

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

### 6.6 Auto-suggestion: "Date" for fields with "date" in the name

`_render_field_logic` in `app.py` computes
`auto_suggested_fields = [f for f in available_fields if "date" in
f.lower() and f not in field_logic]` — any field whose name contains
"date" (case-insensitive substring, e.g. `Created Date`, `Last Modified
Date`) gets `DATE` pre-selected in its dropdown, tagged with a small
purple **"Auto Selected"** ribbon (`.pill-mini.pill-mini-accent` in
`style.css`). This is purely a starting suggestion, not a lock: the
dropdown is fully editable, and the tag disappears the moment the row's
`<select>` fires a `change` event (see the JS in `field_logic.html`) —
once the user touches it, it's their explicit choice either way. Note the
condition `f not in field_logic`: an already-confirmed choice for that
field (from a previous visit / Back-then-forward) is never overridden by
the auto-suggestion.

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
| `Verified` | `MAX_VERIFIED_EXAMPLES` | 3 | No — first N in scan order |
| `Mismatch` | `MAX_MISMATCH_EXAMPLES` | 5 | **Yes** |
| `Accepted-Mismatch` | `MAX_ACCEPTED_MISMATCH_EXAMPLES` | 5 | **Yes** |
| `Unverified` | `MAX_UNVERIFIED_EXAMPLES` | 3 | No — first N in scan order |

**Diversified packing** (`_pack_diverse_examples`): for Mismatch and
Accepted-Mismatch, instead of blindly taking the first N examples in scan
order (which could all coincidentally be the same underlying reason), it
groups by the row's exact `.comments` string and takes one example per
distinct reason first — **exceeding the cap if there are more distinct
reasons than it allows** — then, only if there's still room, pads up to
the cap with more examples of whatever reason. So a Date field with both
"Date differs, time matches" and "Time differs, date matches" mismatches
will show at least one of each, not 5 near-duplicates of whichever
happened to scan first.

Why this matters concretely: Date/Picklist/Number/Case-insensitive logic
introduced *genuinely different reasons* a field can mismatch (unlike the
old plain-STRICT world where "Values differ" was the only reason that
ever existed) — diversified packing is what makes those different reasons
actually visible in the condensed report instead of getting randomly
buried.

**Comments column, per bucket:**

- `Verified` rows have no per-record reason (nothing to explain about an
  exact match) — the Comments column shows one shared scan-summary line
  instead: `"Out of N compared, X verified, Y mismatched, Z
  accepted-mismatch, and W unverified (both blank)."`
- **Every other bucket carries the real, specific per-record reason**
  (e.g. `"Same value, different casing"`, `"Date differs, time matches"`)
  — this was a deliberate fix (previously *all* buckets shared the one
  generic summary line, which for `Accepted-Mismatch` rows meant the
  actual "why" was never shown anywhere in the condensed report — a real
  bug, since Fixed, see conversation history). Each packed example's own
  comment already includes its FULL-mode context suffix (`"(full scan: N
  records, M mismatches)"`) baked in from `_compare_field_full`, so no
  information is lost by dropping the shared summary for these buckets.

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

---

## 14. Optional Graphify developer tooling

This dedicated branch includes an optional, generated Graphify code graph under
`graphify-out/` plus project-local skills for Codex and Claude Code. Graphify is
the preferred first navigation tool for structural questions (architecture,
dependencies, imports, inheritance, callers/callees, execution paths, and
component relationships) when its CLI and graph are available. Its results are
a navigation aid: agents must still inspect the cited source for exact behavior
or before modifying code.

The fallback is deliberate and load-bearing: if Graphify is absent, fails, or
is stale, agents continue with this document, the repository's other Markdown
instructions, and normal search/file-reading tools. Graphify is not listed in
application dependencies and is never required to build, run, test, or develop
the application normally.

Generated artifacts are kept separate from application source. The shareable
graph, report, analysis, and labels may be tracked on this branch;
machine-local timestamp/root-marker/cache/cost/memory/reflection state is
ignored via `.gitignore`. `.graphifyignore` excludes virtual environments,
caches, prior Graphify output, and unrelated generated media from indexing.
