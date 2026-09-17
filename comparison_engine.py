"""Core SOAP vs REST comparison logic.

Every field is compared per its assigned Comparison Logic (Case-insensitive
by default, or Strict/Date/Picklist/Number if explicitly assigned on the
Field Comparison Logic wizard page) -- see PROJECT.md section 6 for the
full design (dispatch order, Accepted-Mismatch rules, comment templates).
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment

DEFAULT_KEY_COLUMNS = ["Internal Id"]
# Normalized (lowercase, alphanumeric-only) forms of the default key that we
# still recognize despite casing/spacing/punctuation differences in the
# sheet's header -- e.g. "Internal ID", "InternalId", "internal_id",
# "InternalId__c" (Salesforce custom-field suffix, after namespace stripping).
DEFAULT_KEY_ALIASES = {"internalid", "internalidc"}

# Default Salesforce managed-package namespace prefixes -- e.g. a custom
# field's API name looks like '<namespace>__FieldName__c'. The same field can
# be installed under a different namespace per org, so SOAP and REST exports
# may show different literal header text for the same field. These are only
# defaults; a user can override them (see normalize_namespaced_columns).
DEFAULT_SOAP_NAMESPACE = "breadwinner_ns"
DEFAULT_REST_NAMESPACE = "rc_breadwinner"

BLANK_TOKENS = {"", "null", "na", "n/a", "-"}

LOGIC_STRICT = "STRICT"
LOGIC_DATE = "DATE"
LOGIC_PICKLIST = "PICKLIST"
LOGIC_NUMBER = "NUMBER"
LOGIC_CASE_INSENSITIVE = "CASE_INSENSITIVE"
LOGIC_BOOLEAN = "BOOLEAN"
# Any field not explicitly assigned a logic uses this -- case-insensitive,
# not STRICT. STRICT is still available as an explicit opt-in choice for
# fields where exact casing genuinely matters.
DEFAULT_LOGIC = LOGIC_CASE_INSENSITIVE


def is_blank(value) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in BLANK_TOKENS


def _is_boolean_false(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() == "false"


def _is_boolean_only_field(soap_by_id: dict, rest_by_id: dict) -> bool:
    """True if every non-blank value for this field, on both sides, is
    exactly 'true' or 'false' (case-insensitive) -- i.e. this is genuinely
    a boolean field, auto-detected from the actual data rather than
    requiring the user to assign BOOLEAN logic by hand. An all-blank field
    doesn't count (nothing to detect from): at least one True/False value
    somewhere is required."""
    values = list(soap_by_id.values()) + list(rest_by_id.values())
    non_blank = [v for v in values if not is_blank(v)]
    if not non_blank:
        return False
    return all(str(v).strip().lower() in ("true", "false") for v in non_blank)


def _is_blank_equivalent(soap_val, rest_val, logic: str) -> bool:
    """True for a genuinely blank-blank pair, OR (BOOLEAN logic only) a
    False-False pair. A boolean field's False is usually just NetSuite's
    default/unset state -- as uninformative as a blank -- so both-False
    gets the same treatment as both-blank: skipped entirely in N-sample
    mode, reported as 'Unverified' rather than 'Verified' in FULL mode.
    True-True is unaffected and still counts as real Verified evidence."""
    if is_blank(soap_val) and is_blank(rest_val):
        return True
    return logic == LOGIC_BOOLEAN and _is_boolean_false(soap_val) and _is_boolean_false(rest_val)


CSV_ENCODING_FALLBACKS = ["utf-8-sig", "cp1252", "latin-1"]


def _read_csv_with_fallback_encoding(file_path: str) -> pd.DataFrame:
    last_error = None
    for encoding in CSV_ENCODING_FALLBACKS:
        try:
            return pd.read_csv(file_path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error


def _read_table(file_path: str) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    filename = os.path.basename(file_path)
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, dtype=str)
            df = df.fillna("")
        else:
            df = _read_csv_with_fallback_encoding(file_path)
    except pd.errors.ParserError as exc:
        raise ValueError(
            f"Couldn't read '{filename}' as CSV: {exc} — this usually means a value contains "
            f"a comma that isn't wrapped in quotes, so a row ends up with the wrong number of "
            f"columns. Check the line number mentioned above and fix or re-export that row."
        ) from exc
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"'{filename}' appears to be empty.") from exc
    df.columns = [str(c).strip() for c in df.columns]
    return df


def load_sheet(file_path: str) -> pd.DataFrame:
    """Loads a SOAP/REST export. No longer assumes a specific key column --
    key selection (Internal Id if present, otherwise user-chosen) happens
    separately, once both sheets are loaded (see has_default_key /
    common_columns / build_reports)."""
    return _read_table(file_path)


def common_columns(soap_df: pd.DataFrame, rest_df: pd.DataFrame) -> list:
    """Columns present in both sheets -- candidates for a matching key."""
    return sorted(set(soap_df.columns) & set(rest_df.columns))


def strip_namespace(header: str, namespace: str) -> str:
    """Strips a Salesforce managed-package namespace wherever it appears in
    the header, not just as a leading prefix -- e.g. a plain field:
    strip_namespace('breadwinner_ns__Status__c', 'breadwinner_ns') ->
    'Status__c'; or a relationship-traversal field, where the namespace sits
    after a '.' rather than at the very start:
    strip_namespace('Bill__r.breadwinner_ns__InternalId__c', 'breadwinner_ns')
    -> 'Bill__r.InternalId__c'. Returns the header unchanged if the namespace
    doesn't appear at all (e.g. standard fields like 'Name')."""
    if not namespace:
        return header
    return header.replace(f"{namespace}__", "")


def normalize_namespaced_columns(soap_df: pd.DataFrame, rest_df: pd.DataFrame, soap_namespace: str, rest_namespace: str):
    """Renames every column on each side that carries that side's namespace
    prefix to its stripped form, so the same underlying field -- installed
    under a different managed-package namespace per org -- lines up by name
    across both sheets (e.g. 'breadwinner_ns__Status__c' and
    'rc_breadwinner__Status__c' both become 'Status__c'). Works on copies;
    returns (soap_df, rest_df) unchanged if nothing matches."""
    soap_rename = {c: strip_namespace(c, soap_namespace) for c in soap_df.columns if strip_namespace(c, soap_namespace) != c}
    rest_rename = {c: strip_namespace(c, rest_namespace) for c in rest_df.columns if strip_namespace(c, rest_namespace) != c}
    if soap_rename:
        soap_df = soap_df.rename(columns=soap_rename)
    if rest_rename:
        rest_df = rest_df.rename(columns=rest_rename)
    return soap_df, rest_df


def _normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_default_key_column(df: pd.DataFrame) -> Optional[str]:
    """Finds a column matching a known default-key alias regardless of
    casing/spacing/punctuation (e.g. 'Internal ID', 'InternalId',
    'internal_id' all match). Returns the column's actual header text as it
    appears in the sheet, or None if no alias matches."""
    for column in df.columns:
        if _normalize_header(column) in DEFAULT_KEY_ALIASES:
            return column
    return None


def resolve_default_key(soap_df: pd.DataFrame, rest_df: pd.DataFrame) -> Optional[tuple]:
    """Returns (soap_column_name, rest_column_name) if both sheets have a
    recognizable Internal-Id-like column -- even if the exact spelling
    differs between the two sheets (e.g. SOAP says 'Internal Id', REST says
    'Internal ID') -- otherwise None."""
    soap_col = _find_default_key_column(soap_df)
    rest_col = _find_default_key_column(rest_df)
    if soap_col and rest_col:
        return soap_col, rest_col
    return None


def normalize_key_column_names(soap_df: pd.DataFrame, rest_df: pd.DataFrame):
    """If both sheets have a recognizable Internal-Id-like column, renames it
    to the canonical 'Internal Id' in both (working on copies) so the rest of
    the pipeline can treat it uniformly -- this also prevents a harmless
    spelling difference on the key column from being flagged as a column
    mismatch warning. Returns (soap_df, rest_df), unchanged if no match."""
    match = resolve_default_key(soap_df, rest_df)
    if not match:
        return soap_df, rest_df
    soap_col, rest_col = match
    canonical = DEFAULT_KEY_COLUMNS[0]
    if soap_col != canonical:
        soap_df = soap_df.rename(columns={soap_col: canonical})
    if rest_col != canonical:
        rest_df = rest_df.rename(columns={rest_col: canonical})
    return soap_df, rest_df


def has_default_key(soap_df: pd.DataFrame, rest_df: pd.DataFrame) -> bool:
    return all(col in soap_df.columns and col in rest_df.columns for col in DEFAULT_KEY_COLUMNS)


def apply_column_mapping(soap_df: pd.DataFrame, rest_df: pd.DataFrame, mapping: dict):
    """Renames REST-side columns to their paired SOAP-side name, per a
    user-chosen {soap_field: rest_field} mapping from the "Map Unmatched
    Fields" page -- reuses the same canonical-rename approach as
    normalize_key_column_names, so downstream code (diff_columns,
    build_reports, etc.) just sees one more ordinary common field, no
    special-casing needed. Works on a copy of rest_df; ignores any mapping
    entry whose rest_field no longer exists (e.g. stale state)."""
    if not mapping:
        return soap_df, rest_df
    rename = {rest_field: soap_field for soap_field, rest_field in mapping.items() if rest_field in rest_df.columns}
    if rename:
        rest_df = rest_df.rename(columns=rename)
    return soap_df, rest_df


def _composite_key_series(df: pd.DataFrame, key_columns: list) -> pd.Series:
    """Builds the per-row matching key. A single key column is used as-is
    (keeps 'Evidence' looking like a plain Internal Id in the common case);
    multiple key columns are combined into a labeled, human-readable string
    so it's still clear which record it refers to in the output."""
    parts = [df[col].astype(str).str.strip() for col in key_columns]
    if len(key_columns) == 1:
        return parts[0]
    labeled = [f"{col}: " + part for col, part in zip(key_columns, parts)]
    combined = labeled[0]
    for extra in labeled[1:]:
        combined = combined.str.cat(extra, sep=", ")
    return combined


def _with_key_column(df: pd.DataFrame, key_columns: list) -> pd.DataFrame:
    df = df.copy()
    df["__key__"] = _composite_key_series(df, key_columns)
    return df


def diff_columns(soap_df: pd.DataFrame, rest_df: pd.DataFrame, key_columns: list, ignored_fields: list = None):
    soap_cols = set(soap_df.columns)
    rest_cols = set(rest_df.columns)
    only_in_soap = sorted(soap_cols - rest_cols)
    only_in_rest = sorted(rest_cols - soap_cols)
    exclude = set(key_columns) | set(ignored_fields or [])
    common_fields = sorted((soap_cols & rest_cols) - exclude)
    return common_fields, only_in_soap, only_in_rest


def match_records(soap_df: pd.DataFrame, rest_df: pd.DataFrame, key_columns: list):
    soap_ids = set(_composite_key_series(soap_df, key_columns))
    rest_ids = set(_composite_key_series(rest_df, key_columns))
    matched_ids = soap_ids & rest_ids
    only_in_soap = soap_ids - rest_ids
    only_in_rest = rest_ids - soap_ids
    return matched_ids, only_in_soap, only_in_rest


@dataclass
class EvidenceRow:
    evidence: str
    field_label: str
    soap_value: str
    rest_value: str
    tested: str
    comments: str = ""


def compare_field(
    field_label: str,
    soap_by_id: dict,
    rest_by_id: dict,
    ordered_matched_ids: list,
    testing_size,
    default_n: int,
    logic: str = DEFAULT_LOGIC,
) -> list:
    if testing_size == "FULL":
        return _compare_field_full(field_label, soap_by_id, rest_by_id, ordered_matched_ids, default_n, logic)
    n = testing_size if isinstance(testing_size, int) else default_n
    return _compare_field_sample(field_label, soap_by_id, rest_by_id, ordered_matched_ids, n, logic)


def _try_parse_date(value: str):
    """Returns a parsed pandas Timestamp, or None if the value isn't a
    recognizable date/time at all (in which case DATE logic falls back to a
    plain strict compare -- see _classify_date)."""
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (ValueError, TypeError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _classify_date(soap_val: str, rest_val: str):
    """Called only when the raw strings already differ and neither side is
    blank. Same date+time, different format -> Accepted-Mismatch (benign);
    a genuine difference in date and/or time -> a real Mismatch, with a
    comment naming which part actually differs."""
    soap_dt = _try_parse_date(soap_val)
    rest_dt = _try_parse_date(rest_val)
    if soap_dt is None or rest_dt is None:
        return "Mismatch", "Values differ"
    if soap_dt == rest_dt:
        return "Accepted-Mismatch", "Same date and time — only the format differs"
    date_equal = soap_dt.date() == rest_dt.date()
    time_equal = soap_dt.time() == rest_dt.time()
    if date_equal and not time_equal:
        return "Mismatch", "Time differs, date matches"
    if not date_equal and time_equal:
        return "Mismatch", "Date differs, time matches"
    return "Mismatch", "Values differ"


def _classify_picklist(soap_val: str, rest_val: str):
    """Called only when the raw strings already differ and neither side is
    blank. Same set of values, different order -> Accepted-Mismatch
    (benign); a genuinely different set of values -> a real Mismatch."""
    soap_items = sorted(p.strip() for p in re.split(r"[;,]", soap_val) if p.strip())
    rest_items = sorted(p.strip() for p in re.split(r"[;,]", rest_val) if p.strip())
    if soap_items == rest_items:
        return "Accepted-Mismatch", "Same values, different order"
    return "Mismatch", "Values differ"


def _classify_case_insensitive(soap_val: str, rest_val: str):
    """Called only when the raw strings already differ and neither side is
    blank. Same value, only the casing differs -> Accepted-Mismatch
    (benign); a genuine difference even ignoring case -> a real Mismatch.
    This is the DEFAULT logic for any field not explicitly assigned one."""
    if soap_val.lower() == rest_val.lower():
        return "Accepted-Mismatch", "Same value, different casing"
    return "Mismatch", "Values differ"


def _try_parse_number(value: str):
    """Returns a float, or None if the value isn't a recognizable number at
    all (in which case NUMBER logic falls back to a plain strict compare --
    see _classify_number). Strips thousands-separator commas and
    surrounding whitespace first, so '1,000' and ' 100 ' still parse."""
    try:
        cleaned = value.strip().replace(",", "")
        if cleaned == "":
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _classify_number(soap_val: str, rest_val: str):
    """Called only when the raw strings already differ and neither side is
    blank. Same numeric value, different formatting (leading/trailing
    zeros, decimal precision -- e.g. integer vs double, thousands
    separators, stray whitespace) -> Accepted-Mismatch (benign); a genuine
    numeric difference, or either side not parseable as a number at all,
    -> a real Mismatch."""
    soap_num = _try_parse_number(soap_val)
    rest_num = _try_parse_number(rest_val)
    if soap_num is None or rest_num is None:
        return "Mismatch", "Values differ"
    if soap_num == rest_num:
        return "Accepted-Mismatch", "Same number, only formatting differs"
    return "Mismatch", "Values differ"


def _classify_default(soap_val: str, rest_val: str):
    """Called only when the raw strings already differ and neither side is
    blank, for a field left at the DEFAULT (unassigned) logic -- fields
    explicitly assigned DATE/PICKLIST/NUMBER/STRICT use only their own
    check, never this one (see PROJECT.md 6.3a). Gives the default path the
    'benefit of the doubt' across all three benign-difference checks, tried
    in order, before finally calling it a real Mismatch: case-insensitive
    (same value, different casing), then Picklist-style (same values,
    different order), then Number-style (same number, different
    formatting/precision). Each sub-check's own Mismatch fallback text is
    discarded here -- only an Accepted-Mismatch result from any of them is
    taken; if all three fail to explain the difference, the value is a
    genuine Mismatch."""
    if soap_val.lower() == rest_val.lower():
        return "Accepted-Mismatch", "Same value, different casing"
    tested, comment = _classify_picklist(soap_val, rest_val)
    if tested == "Accepted-Mismatch":
        return tested, comment
    tested, comment = _classify_number(soap_val, rest_val)
    if tested == "Accepted-Mismatch":
        return tested, comment
    return "Mismatch", "Values differ"


def _classify(soap_val: str, rest_val: str, logic: str = DEFAULT_LOGIC):
    """Returns (tested, comment) for a pair of non-both-blank values."""
    soap_blank = is_blank(soap_val)
    rest_blank = is_blank(rest_val)
    if soap_blank and not rest_blank:
        return "Mismatch", "SOAP is blank, REST has a value"
    if rest_blank and not soap_blank:
        return "Mismatch", "REST is blank, SOAP has a value"
    if soap_val == rest_val:
        return "Verified", ""
    if logic == LOGIC_DATE:
        return _classify_date(soap_val, rest_val)
    if logic == LOGIC_PICKLIST:
        return _classify_picklist(soap_val, rest_val)
    if logic == LOGIC_NUMBER:
        return _classify_number(soap_val, rest_val)
    if logic == LOGIC_STRICT:
        return "Mismatch", "Values differ"
    if logic == LOGIC_BOOLEAN:
        # No benefit of the doubt for a boolean disagreement (e.g. True vs
        # False) -- only casing leniency, same as before. Order/number
        # checks make no sense for a two-value field and could only ever
        # mask a genuine True-vs-False mismatch, which must stay a real
        # Mismatch (see PROJECT.md 6.3a).
        return _classify_case_insensitive(soap_val, rest_val)
    return _classify_default(soap_val, rest_val)


def _compare_field_sample(field_label, soap_by_id, rest_by_id, ordered_matched_ids, n, logic=DEFAULT_LOGIC) -> list:
    rows = []
    for record_id in ordered_matched_ids:
        if len(rows) >= n:
            break
        soap_val = soap_by_id.get(record_id, "")
        rest_val = rest_by_id.get(record_id, "")
        if _is_blank_equivalent(soap_val, rest_val, logic):
            continue
        tested, comment = _classify(soap_val, rest_val, logic)
        rows.append(EvidenceRow(record_id, field_label, soap_val, rest_val, tested, comment))
    return rows


def _compare_field_full(field_label, soap_by_id, rest_by_id, ordered_matched_ids, default_n, logic=DEFAULT_LOGIC) -> list:
    """FULL mode always returns every matched record for this field -- never
    capped, regardless of outcome. Unlike sample mode (which skips
    both-blank pairs entirely), FULL mode reports them too, as 'Unverified'
    -- a distinct third outcome from Verified/Mismatch, since neither side
    actually had a value to compare."""
    rows = []
    total = len(ordered_matched_ids)
    for record_id in ordered_matched_ids:
        soap_val = soap_by_id.get(record_id, "")
        rest_val = rest_by_id.get(record_id, "")
        if _is_blank_equivalent(soap_val, rest_val, logic):
            reason = "Both blank" if is_blank(soap_val) and is_blank(rest_val) else "Both False"
            rows.append(EvidenceRow(record_id, field_label, soap_val, rest_val, "Unverified", reason))
            continue
        tested, comment = _classify(soap_val, rest_val, logic)
        rows.append(EvidenceRow(record_id, field_label, soap_val, rest_val, tested, comment))

    mismatch_count = sum(1 for row in rows if row.tested == "Mismatch")
    suffix = f"full scan: {total} records, {mismatch_count} mismatches"
    for row in rows:
        row.comments = f"{row.comments} ({suffix})" if row.comments else suffix
    return rows


def build_reports(
    soap_df: pd.DataFrame,
    rest_df: pd.DataFrame,
    default_n: int,
    key_columns: list,
    full_by_default: bool = False,
    ignored_fields: list = None,
    field_logic: dict = None,
):
    """default_n: Testing Size for every field (also caps how many 'Verified'
    rows a clean FULL-mode field shows). full_by_default: if True, every
    field defaults to Testing Size = FULL instead of default_n.
    key_columns: one or more column names (present in both sheets) used to
    match a SOAP row to its REST row -- 'Internal Id' when available (see
    has_default_key), otherwise chosen by the user. ignored_fields: fields
    the user explicitly chose to exclude from comparison entirely (e.g.
    lookup/Id fields that are expected to always differ). field_logic:
    {field_label: 'DATE'|'PICKLIST'|...} for fields the user assigned a
    non-default comparison logic to -- any field not listed uses
    DEFAULT_LOGIC (case-insensitive). A field auto-detected as boolean
    (see _is_boolean_only_field) always gets BOOLEAN logic regardless of
    what's in field_logic, including overriding an explicit assignment --
    BOOLEAN isn't a manual dropdown choice at all (see PROJECT.md §6.4a)."""
    common_fields, only_in_soap, only_in_rest = diff_columns(soap_df, rest_df, key_columns, ignored_fields)
    matched_ids, only_in_soap_ids, only_in_rest_ids = match_records(soap_df, rest_df, key_columns)
    field_logic = field_logic or {}

    soap_keyed = _with_key_column(soap_df, key_columns)
    rest_keyed = _with_key_column(rest_df, key_columns)
    soap_indexed = soap_keyed.set_index("__key__", drop=False)
    rest_indexed = rest_keyed.set_index("__key__", drop=False)
    ordered_matched_ids = [rid for rid in soap_keyed["__key__"] if rid in matched_ids]

    default_testing_size = "FULL" if full_by_default else default_n

    comparison_rows = []
    field_summary = {}
    for field_label in common_fields:
        soap_by_id = soap_indexed[field_label].to_dict()
        rest_by_id = rest_indexed[field_label].to_dict()
        testing_size = default_testing_size
        logic = field_logic.get(field_label, DEFAULT_LOGIC)
        if _is_boolean_only_field(soap_by_id, rest_by_id):
            logic = LOGIC_BOOLEAN
        field_rows = compare_field(field_label, soap_by_id, rest_by_id, ordered_matched_ids, testing_size, default_n, logic)
        comparison_rows.extend(field_rows)

        has_mismatch = any(r.tested == "Mismatch" for r in field_rows)
        has_accepted = any(r.tested == "Accepted-Mismatch" for r in field_rows)
        has_verified = any(r.tested == "Verified" for r in field_rows)
        if has_mismatch:
            status = "Failed"
        elif has_accepted:
            status = "Accepted-Mismatch"
        elif has_verified:
            status = "Passed"
        else:
            status = "Unverified"
        field_summary[field_label] = {"status": status, "testing_size": testing_size}

    missing_rows = []
    for record_id in sorted(only_in_soap_ids):
        missing_rows.append({"Record Key": record_id, "Missing In": "REST", "Comments": "REST did not return this record."})
    for record_id in sorted(only_in_rest_ids):
        missing_rows.append({"Record Key": record_id, "Missing In": "SOAP", "Comments": "SOAP did not return this record."})

    column_diff = {"only_in_soap": only_in_soap, "only_in_rest": only_in_rest}

    return comparison_rows, missing_rows, column_diff, field_summary


def build_negative_case_report(soap_df: pd.DataFrame, rest_df: pd.DataFrame, key_columns: list, ignored_fields: list = None):
    """Negative-case testing (see PROJECT.md section 7): exhaustively scans every
    matched record for every common field, reporting every case where SOAP
    has a value but REST is blank -- i.e. REST silently failing to populate
    a field it should have. Unlike the main report, this is not sampled or
    capped, and only checks this one direction (not the reverse). ignored_fields:
    fields explicitly excluded from the scan (e.g. lookup/Id fields expected
    to always differ)."""
    common_fields, only_in_soap, only_in_rest = diff_columns(soap_df, rest_df, key_columns, ignored_fields)
    matched_ids, _, _ = match_records(soap_df, rest_df, key_columns)

    soap_keyed = _with_key_column(soap_df, key_columns)
    rest_keyed = _with_key_column(rest_df, key_columns)
    soap_indexed = soap_keyed.set_index("__key__", drop=False)
    rest_indexed = rest_keyed.set_index("__key__", drop=False)
    ordered_matched_ids = [rid for rid in soap_keyed["__key__"] if rid in matched_ids]

    rows = []
    for field_label in common_fields:
        soap_by_id = soap_indexed[field_label].to_dict()
        rest_by_id = rest_indexed[field_label].to_dict()
        for record_id in ordered_matched_ids:
            soap_val = soap_by_id.get(record_id, "")
            rest_val = rest_by_id.get(record_id, "")
            if not is_blank(soap_val) and is_blank(rest_val):
                rows.append(
                    EvidenceRow(
                        record_id,
                        field_label,
                        soap_val,
                        rest_val,
                        "Mismatch",
                        "REST is blank, SOAP has a value (negative case)",
                    )
                )

    column_diff = {"only_in_soap": only_in_soap, "only_in_rest": only_in_rest}
    return rows, column_diff


COMPARISON_COLUMNS = ["Field Label", "Evidence", "Soap Value", "Rest Value", "Tested", "Comments"]

MAX_VERIFIED_EXAMPLES = 3
MAX_MISMATCH_EXAMPLES = 5
MAX_ACCEPTED_MISMATCH_EXAMPLES = 5
MAX_UNVERIFIED_EXAMPLES = 3


def _pack_diverse_examples(bucket: list, cap: int, key=lambda r: r.comments, allow_exceed_cap: bool = True) -> list:
    """Prefers one example per distinct `key(row)` before repeating any key.

    `allow_exceed_cap=True` (the default, used for Mismatch/Accepted-Mismatch,
    keyed on `.comments`) exceeds `cap` if there are more distinct keys than
    it allows -- safe there because a field only ever has a handful of
    possible *reason* strings (e.g. 'Date differs, time matches' vs 'Values
    differ'), so this shows at least one of each instead of `cap`
    near-duplicates of whichever reason happened to appear first.

    `allow_exceed_cap=False` (used for Verified, keyed on `.soap_value`)
    still prefers one example per distinct *value* first, but never returns
    more than `cap` -- required there because a value (unlike a reason) can
    have unbounded cardinality (numbers, names, ids...), so "exceed the cap
    for diversity" would defeat the cap entirely, returning nearly every
    record instead of the small sample the report is supposed to show."""
    if not bucket:
        return []
    by_reason = {}
    order = []
    for r in bucket:
        k = key(r)
        if k not in by_reason:
            by_reason[k] = []
            order.append(k)
        by_reason[k].append(r)

    picked = [by_reason[reason][0] for reason in order]
    if not allow_exceed_cap and len(picked) > cap:
        return picked[:cap]
    if len(picked) >= cap:
        return picked  # diversity alone already meets/exceeds the cap
    picked_ids = {id(r) for r in picked}
    for r in bucket:
        if len(picked) >= cap:
            break
        if id(r) not in picked_ids:
            picked.append(r)
            picked_ids.add(id(r))
    return picked


@dataclass
class CondensedRow:
    field_label: str
    tested: str
    soap_values: list
    rest_values: list
    evidences: list
    comments: list


def condense_rows(rows: list) -> list:
    """Display-only condensing (REQUIREMENTS: no change to the underlying
    scan/comparison logic -- this just reformats the already-gathered
    EvidenceRow list for the download). Groups the flat per-record rows by
    field (fields are already contiguous, in scan order) and collapses each
    field down to at most 4 rows: 'Verified' (up to MAX_VERIFIED_EXAMPLES),
    'Mismatch' (up to MAX_MISMATCH_EXAMPLES, diversified -- see
    _pack_diverse_examples), 'Accepted-Mismatch' (up to
    MAX_ACCEPTED_MISMATCH_EXAMPLES, diversified), and 'Unverified' (both
    sides blank -- FULL mode only, up to MAX_UNVERIFIED_EXAMPLES).

    Comments: Verified rows have no per-record reason (an exact match needs
    no explanation), so they show one shared scan-summary line instead. All
    other buckets carry a real, specific reason per packed example (e.g.
    'Same value, different casing', 'Date differs, time matches') --
    exactly the comment already computed for that record -- joined
    alongside its Soap/Rest/Evidence values, so the report never falls back
    to a vague aggregate when a genuine per-record explanation exists."""
    condensed = []
    i = 0
    n = len(rows)
    while i < n:
        field_label = rows[i].field_label
        j = i
        while j < n and rows[j].field_label == field_label:
            j += 1
        group = rows[i:j]
        i = j

        verified = [r for r in group if r.tested == "Verified"]
        mismatched = [r for r in group if r.tested == "Mismatch"]
        accepted = [r for r in group if r.tested == "Accepted-Mismatch"]
        unverified = [r for r in group if r.tested == "Unverified"]
        total = len(group)
        unverified_reasons = {
            "False" if r.comments.startswith("Both False") else "blank" for r in unverified
        }
        if not unverified_reasons:
            unverified_desc = "both blank"  # count is 0 either way, wording is moot
        elif unverified_reasons == {"False"}:
            unverified_desc = "both False"
        elif unverified_reasons == {"blank"}:
            unverified_desc = "both blank"
        else:
            unverified_desc = "both blank or both False"
        summary = (
            f"Out of {total} compared, {len(verified)} verified, {len(mismatched)} mismatched, "
            f"{len(accepted)} accepted-mismatch, and {len(unverified)} unverified ({unverified_desc})."
        )

        # The shared summary line is guaranteed to appear on Verified or
        # Unverified whenever either exists. If a field has neither (only
        # Mismatch and/or Accepted-Mismatch rows), it would otherwise never
        # appear anywhere -- so it gets prepended to whichever of those two
        # buckets is emitted first instead, tracked here.
        summary_needs_injection = not verified and not unverified
        injected_summary = False

        for tested, bucket, cap, diversify_key in (
            ("Verified", verified, MAX_VERIFIED_EXAMPLES, lambda r: r.soap_value),
            ("Mismatch", mismatched, MAX_MISMATCH_EXAMPLES, lambda r: r.comments),
            ("Accepted-Mismatch", accepted, MAX_ACCEPTED_MISMATCH_EXAMPLES, lambda r: r.comments),
            ("Unverified", unverified, MAX_UNVERIFIED_EXAMPLES, None),
        ):
            if not bucket:
                continue
            if diversify_key:
                sample = _pack_diverse_examples(bucket, cap, diversify_key, allow_exceed_cap=(tested != "Verified"))
            else:
                sample = bucket[:cap]

            if tested in ("Verified", "Unverified"):
                comments = [summary]
            else:
                # Mismatch / Accepted-Mismatch: a uniform reason across every
                # packed example is shown once, not repeated per example
                # (same principle as Verified/Unverified's single summary
                # line) -- only genuinely distinct reasons get their own line.
                distinct_reasons = {r.comments for r in sample}
                comments = [sample[0].comments] if len(distinct_reasons) == 1 else [r.comments for r in sample]
                if summary_needs_injection and not injected_summary:
                    comments = [summary] + comments
                    injected_summary = True

            condensed.append(
                CondensedRow(
                    field_label,
                    tested,
                    [r.soap_value for r in sample],
                    [r.rest_value for r in sample],
                    [r.evidence for r in sample],
                    comments,
                )
            )
    return condensed


def condensed_to_dataframe(condensed_rows: list, separator: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Field Label": r.field_label,
                "Evidence": separator.join(r.evidences),
                "Soap Value": separator.join(r.soap_values),
                "Rest Value": separator.join(r.rest_values),
                "Tested": r.tested,
                "Comments": separator.join(r.comments),
            }
            for r in condensed_rows
        ],
        columns=COMPARISON_COLUMNS,
    )


def rows_to_dataframe(rows: list, separator: str = ", ") -> pd.DataFrame:
    """Builds the final display DataFrame: condenses the flat evidence list
    down to at most 2 rows per field (see condense_rows), joining packed
    example values with the given separator (', ' for CSV, '\\n' for Excel
    cells with wrap-text)."""
    return condensed_to_dataframe(condense_rows(rows), separator)


def flat_rows_to_dataframe(rows: list) -> pd.DataFrame:
    """One row per record, no condensing -- used for the Negative Case
    report, which is meant to exhaustively list every occurrence, not
    summarize it."""
    return pd.DataFrame(
        [
            {
                "Evidence": r.evidence,
                "Field Label": r.field_label,
                "Soap Value": r.soap_value,
                "Rest Value": r.rest_value,
                "Tested": r.tested,
                "Comments": r.comments,
            }
            for r in rows
        ],
        columns=COMPARISON_COLUMNS,
    )


MISMATCHED_RECORDS_COLUMNS = ["Field Label", "Evidence", "Soap Value", "Rest Value", "Comments"]


def mismatched_rows_to_dataframe(rows: list) -> pd.DataFrame:
    """Exhaustive, uncapped list of every Mismatch found (no condensing, no
    5-example cap like the main report) -- the Mismatched Records report.
    Excludes Verified/Unverified rows entirely, and drops the 'Tested'
    column since every row here is a mismatch by definition."""
    mismatches = [r for r in rows if r.tested == "Mismatch"]
    return pd.DataFrame(
        [
            {
                "Field Label": r.field_label,
                "Evidence": r.evidence,
                "Soap Value": r.soap_value,
                "Rest Value": r.rest_value,
                "Comments": r.comments,
            }
            for r in mismatches
        ],
        columns=MISMATCHED_RECORDS_COLUMNS,
    )


def mismatch_summary_lines(rows: list) -> list:
    """Returns ['Field -> N mismatched', ...] -- one line per field that had
    at least one mismatch, in scan order, with the count from the full
    (uncapped) evidence list. Meant to be appended at the end of the
    Detailed Mismatched Records Report as a quick per-field tally."""
    counts = {}
    for r in rows:
        if r.tested == "Mismatch":
            counts[r.field_label] = counts.get(r.field_label, 0) + 1
    return [f"{field} -> {count} mismatched" for field, count in counts.items()]


def append_lines_to_csv(path: str, lines: list) -> None:
    """Appends a blank separator line followed by each of `lines` (plain
    text, one per row) to an already-written CSV file."""
    if not lines:
        return
    with open(path, "a", newline="") as f:
        f.write("\n")
        for line in lines:
            f.write(line + "\n")


def blank_repeated_field_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Grouped-report style: only show 'Field Label' on the first row of each
    field's consecutive block, blanking it out on the rest for readability."""
    df = df.copy()
    is_first_in_group = df["Field Label"] != df["Field Label"].shift()
    df.loc[~is_first_in_group, "Field Label"] = ""
    return df


def write_comparison_excel(df: pd.DataFrame, path: str, extra_lines: list = None) -> None:
    """Writes the field comparison report as .xlsx with the 'Field Label'
    column merged/centered across each field's consecutive evidence rows.
    extra_lines (optional): plain text lines appended at the end of the
    sheet, one per row, after the trailing blank row -- used for the
    Detailed Mismatched Records Report's per-field mismatch-count summary."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Field Comparison"

    headers = list(df.columns)
    ws.append(headers)
    field_col_idx = headers.index("Field Label") + 1

    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        ws.append(list(row))
        max_lines = max((str(v).count("\n") + 1 for v in row), default=1)
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col_idx).alignment = Alignment(wrap_text=True, vertical="top")
        if max_lines > 1:
            ws.row_dimensions[row_idx].height = max(15, 14 * max_lines)

    field_values = df["Field Label"].tolist()
    n = len(field_values)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and field_values[j + 1] == field_values[i]:
            j += 1
        start_row, end_row = i + 2, j + 2  # +2: header row + 1-based indexing
        if end_row > start_row:
            ws.merge_cells(start_row=start_row, start_column=field_col_idx, end_row=end_row, end_column=field_col_idx)
        ws.cell(row=start_row, column=field_col_idx).alignment = Alignment(wrap_text=True, vertical="center")
        i = j + 1

    for idx, header in enumerate(headers, start=1):
        column_letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[column_letter].width = max(15, min(40, len(header) + 2))

    # Trailing blank row -- ws.append([]) writes no cell values, so it isn't
    # recognized as a real row by Excel/openpyxl; an explicit empty string
    # forces the row to actually exist in the saved sheet.
    ws.cell(row=ws.max_row + 1, column=1, value="")

    if extra_lines:
        for line in extra_lines:
            ws.append([line])

    wb.save(path)


def write_simple_excel(df: pd.DataFrame, path: str, sheet_title: str = "Sheet1") -> None:
    """Plain .xlsx writer (no field-grouping/merging) -- used for reports
    like Missing Records that don't have a 'Field Label' column to merge on."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title

    headers = list(df.columns)
    ws.append(headers)

    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        ws.append(list(row))
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col_idx).alignment = Alignment(wrap_text=True, vertical="top")

    for idx, header in enumerate(headers, start=1):
        column_letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[column_letter].width = max(15, min(40, len(header) + 2))

    ws.cell(row=ws.max_row + 1, column=1, value="")

    wb.save(path)
