# Turing Engine

**Compare · Validate · Migrate**

A local web tool for QA-testing a SOAP → REST middleware migration
(NetSuite ↔ Salesforce). Upload a SOAP-connected export and a
REST-connected export of the same Salesforce object, walk through a short
wizard (map any renamed columns, pick a matching key, drop fields you
don't want checked, assign smarter comparison rules to specific fields),
and get back a set of downloadable reports showing exactly which fields
match, which genuinely differ, and which differ only in a benign way
(date format, casing, value order, number formatting).

For the full architecture, design decisions, and how to extend this
project, see **[`PROJECT.md`](./PROJECT.md)** — that's the maintained
technical reference, kept in sync with the code. This README only covers
getting it running.

---

## Requirements

- Python 3.9 or newer
- No external services, no database, no API keys — everything runs
  locally in memory

## Setup

### macOS / Linux

```bash
git clone <repo-url>
cd soap-rest-data-comparision

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
git clone <repo-url>
cd soap-rest-data-comparision

python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

> Always install into a virtual environment, not your system Python.
> `.venv/` is gitignored — you create it fresh on every machine, it's
> never part of the clone.

## Running it

With the venv activated:

```bash
python app.py
```

Then open **http://localhost:5000** in your browser.

The server prints a warning that it's a development server — that's
expected. This tool is built for local, single-user use (see
`PROJECT.md` §12 for why it isn't meant to be deployed as-is behind
multiple concurrent users).

To stop it: `Ctrl+C` in the terminal, or, if it's still bound to the port
from a previous run:

```bash
# macOS / Linux
lsof -ti:5000 | xargs kill -9

# Windows (PowerShell)
Get-Process -Id (Get-NetTCPConnection -LocalPort 5000).OwningProcess | Stop-Process -Force
```

## Using it

1. **Upload** — pick the SOAP export and REST export for one Salesforce
   object (CSV or Excel), give the object a name, and optionally open
   **Advanced options** to set namespaces, evidence-sampling settings, or
   turn off any wizard step you don't want to see every time.
2. **Map Unmatched Fields** — if some columns don't share an identical
   name across the two exports (e.g. renamed during the REST migration),
   pair them up here so they're compared as the same field.
3. **Select Matching Key** — pick which column (usually `Internal Id`)
   uniquely identifies a record on both sides. Pre-selected automatically
   if found.
4. **Drop Fields** — exclude any fields you already know won't match for
   expected reasons (lookup/Id fields that differ by design, etc.).
5. **Field Comparison Logic** — every field compares case-insensitively by
   default; assign `Strict`, `Date`, `Picklist (order-insensitive)`, or
   `Number` to specific fields that need smarter handling. Date-named
   fields get `Date` pre-suggested automatically (still editable).
6. **Ready to Run** — review the summary, then start the comparison.
7. **Results** — a dashboard of fields Passed / Failed / with Accepted
   (benign) Differences / Unverified, plus downloadable reports:
   - **Field Comparison Report** — condensed, with example evidence per
     field
   - **Missing Records Report** — records present in only one export
   - **Detailed Mismatched Records Report** — every real mismatch,
     uncapped

There's also a **Negative Case Testing** tab on the Upload page, for an
exhaustive scan of "SOAP has a value, REST silently returned blank" —
useful for catching a REST migration silently dropping data.

Every wizard step (except Upload and Results) has Back/Next navigation
that preserves your choices, including across multiple steps.

## Project structure

See `PROJECT.md` §2 for the full architecture breakdown. In short:

```
app.py                 Flask routes + wizard flow control
comparison_engine.py   All comparison logic (no Flask dependency)
templates/             One page per wizard step
static/                Stylesheet, theme toggle, cinematic Start-button assets, logo
```

## Contributing / extending

Read `PROJECT.md` in full before making changes — it documents *why*
things are built the way they are, not just *what* the code does, and
includes a checklist for what to update when you add a new wizard step or
comparison logic type. **Keep it updated as you go** — it's this
project's only persistent memory across sessions.
