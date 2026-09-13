import json
import os
import re
import tempfile
import uuid

import pandas as pd
from flask import Flask, render_template, request, send_file, abort

import comparison_engine as engine

app = Flask(__name__)


@app.context_processor
def inject_asset_version():
    def asset_version(rel_path="style.css"):
        try:
            return int(os.path.getmtime(os.path.join(app.static_folder, rel_path)))
        except OSError:
            return 0

    return {"asset_version": asset_version("style.css"), "asset_version_for": asset_version}

# In-memory store of completed runs: request_id -> {output file paths..., "object_name": str}
RUNS = {}

# In-memory store of uploads awaiting a key-column decision (no default key
# found): pending_id -> {"flow": "compare"|"negative", file paths, form fields}
PENDING = {}


def _save_upload(file_storage, dest_dir: str) -> str:
    filename = file_storage.filename
    path = os.path.join(dest_dir, filename)
    file_storage.save(path)
    return path


def _safe_filename_part(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    return cleaned.strip("_") or "Object"


def _resolve_namespace(raw_value: str, default: str) -> str:
    raw_value = (raw_value or "").strip()
    return raw_value if raw_value else default


def _render_select_key(pending_id, soap_df, rest_df, object_name, selected_keys=None, error=None):
    columns = engine.common_columns(soap_df, rest_df)
    return render_template(
        "select_key.html",
        pending_id=pending_id,
        object_name=object_name,
        common_columns=columns,
        selected_keys=selected_keys or [],
        error=error,
    )


def _render_options(pending_id, object_name, flow, ignored_fields=None):
    run_label = "Start Turing Engine" if flow == "compare" else "Start Turing Engine (Negative Case Test)"
    return render_template(
        "options.html",
        pending_id=pending_id,
        object_name=object_name,
        run_label=run_label,
        ignored_fields=ignored_fields or [],
        back_target="/back-to-field-logic",
    )


def _render_drop_fields(pending_id, soap_df, rest_df, key_columns, object_name, ignored_fields=None):
    available_fields, _, _ = engine.diff_columns(soap_df, rest_df, key_columns)
    return render_template(
        "drop_fields.html",
        pending_id=pending_id,
        object_name=object_name,
        available_fields=available_fields,
        ignored_fields=ignored_fields or [],
    )


def _render_field_logic(pending_id, soap_df, rest_df, key_columns, object_name, ignored_fields=None, field_logic=None):
    available_fields, _, _ = engine.diff_columns(soap_df, rest_df, key_columns, ignored_fields)
    field_logic = field_logic or {}
    # Any field with "date" in its name gets Date logic pre-selected as a
    # starting suggestion (tagged "Auto Selected" in the UI) -- but only if
    # the user hasn't already made an explicit choice for it; once
    # confirmed (even left as-is), it's stored like any other choice.
    auto_suggested_fields = [f for f in available_fields if "date" in f.lower() and f not in field_logic]
    return render_template(
        "field_logic.html",
        pending_id=pending_id,
        object_name=object_name,
        available_fields=available_fields,
        field_logic=field_logic,
        auto_suggested_fields=auto_suggested_fields,
    )


def _render_map_columns(pending_id, soap_df, rest_df, key_columns, object_name, mapping=None):
    common_fields, only_in_soap, only_in_rest = engine.diff_columns(soap_df, rest_df, key_columns)
    return render_template(
        "map_columns.html",
        pending_id=pending_id,
        object_name=object_name,
        auto_matched_fields=common_fields,
        only_in_soap=only_in_soap,
        only_in_rest=only_in_rest,
        mapping=mapping or {},
    )


def _advance_after_upload(pending_id, soap_df, rest_df, object_name, pending):
    """Entry point right after upload -- decides whether to show Map
    Unmatched Fields or skip straight past it, then falls through the rest
    of the pipeline the same way every other entry point does."""
    _, only_in_soap, only_in_rest = engine.diff_columns(soap_df, rest_df, [])
    needs_mapping = bool(only_in_soap or only_in_rest)
    if pending.get("always_show_mapping", True) or needs_mapping:
        return _render_map_columns(
            pending_id, soap_df, rest_df, [], object_name, mapping=pending.get("column_mapping", {})
        )
    return _advance_after_mapping(pending_id, soap_df, rest_df, object_name, pending)


def _advance_after_mapping(pending_id, soap_df, rest_df, object_name, pending):
    """Called once mapping is settled (either just confirmed, or skipped
    because it wasn't needed) -- decides whether to show Select Matching
    Key or skip straight past it (default key already found)."""
    soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))
    has_default_key = engine.has_default_key(soap_df, rest_df)
    if pending.get("always_show_key", True) or not has_default_key:
        default_selection = (
            engine.DEFAULT_KEY_COLUMNS if has_default_key else pending.get("key_columns", [])
        )
        return _render_select_key(pending_id, soap_df, rest_df, object_name, selected_keys=default_selection)
    pending["key_columns"] = engine.DEFAULT_KEY_COLUMNS
    return _advance_after_key(pending_id, soap_df, rest_df, object_name, pending)


def _advance_after_key(pending_id, soap_df, rest_df, object_name, pending):
    """Called once the matching key is settled -- decides whether to show
    Drop Fields or skip straight to the next step. Unlike mapping/key,
    there's no way to detect whether dropping fields is "needed" -- it's
    purely the user's choice via the Advanced options checkbox."""
    if pending.get("always_show_drop", True):
        return _render_drop_fields(
            pending_id, soap_df, rest_df, pending["key_columns"], object_name,
            ignored_fields=pending.get("ignored_fields", []),
        )
    pending["ignored_fields"] = pending.get("ignored_fields", [])
    return _advance_after_drop(pending_id, soap_df, rest_df, object_name, pending)


def _advance_after_drop(pending_id, soap_df, rest_df, object_name, pending):
    """Called once dropped fields are settled -- decides whether to show
    Field Comparison Logic or skip straight to Ready to Run. Like Drop
    Fields, there's no way to detect whether this is "needed" -- purely
    the user's choice via the Advanced options checkbox. Compare-flow only:
    the negative-case scan has its own hardcoded check (SOAP has a value,
    REST is blank) that never consults field_logic, so assigning Date/
    Picklist logic there would be a no-op -- skip the step entirely."""
    if pending["flow"] == "compare" and pending.get("always_show_field_logic", True):
        return _render_field_logic(
            pending_id, soap_df, rest_df, pending["key_columns"], object_name,
            ignored_fields=pending.get("ignored_fields", []),
            field_logic=pending.get("field_logic", {}),
        )
    return _render_options(pending_id, object_name, pending["flow"], ignored_fields=pending.get("ignored_fields", []))


def _render_upload_with_prefill(pending_id, pending):
    prefill = {
        "flow": pending["flow"],
        "existing_pending_id": pending_id,
        "object_name": pending["object_name"],
        "soap_namespace": pending["soap_namespace"],
        "rest_namespace": pending["rest_namespace"],
        "soap_filename": os.path.basename(pending["soap_path"]),
        "rest_filename": os.path.basename(pending["rest_path"]),
        "default_n": pending.get("default_n", 5),
        "full_by_default": pending.get("full_by_default", True),
        "always_show_mapping": pending.get("always_show_mapping", True),
        "always_show_key": pending.get("always_show_key", True),
        "always_show_drop": pending.get("always_show_drop", True),
        "always_show_field_logic": pending.get("always_show_field_logic", True),
    }
    return render_template("index.html", active_tab=pending["flow"], prefill=prefill)


def _retreat_to_mapping_or_earlier(pending_id, pending):
    """Going Back from Select Matching Key: show Map Unmatched Fields if
    it would actually have been shown going forward, otherwise cascade
    further back to Upload -- mirrors _advance_after_upload so Back never
    reveals a page the skip settings would have hidden."""
    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    _, only_in_soap, only_in_rest = engine.diff_columns(soap_df, rest_df, [])
    needs_mapping = bool(only_in_soap or only_in_rest)
    if pending.get("always_show_mapping", True) or needs_mapping:
        return _render_map_columns(
            pending_id, soap_df, rest_df, [], pending["object_name"], mapping=pending.get("column_mapping", {})
        )
    return _render_upload_with_prefill(pending_id, pending)


def _retreat_to_key_or_earlier(pending_id, pending):
    """Going Back from Drop Fields: show Select Matching Key if it would
    actually have been shown going forward, otherwise cascade further
    back to Map (or Upload)."""
    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))
    has_default_key = engine.has_default_key(soap_df, rest_df)
    if pending.get("always_show_key", True) or not has_default_key:
        return _render_select_key(
            pending_id, soap_df, rest_df, pending["object_name"], selected_keys=pending.get("key_columns", [])
        )
    return _retreat_to_mapping_or_earlier(pending_id, pending)


def _retreat_to_drop_or_earlier(pending_id, pending):
    """Going Back from Ready to Run: show Drop Fields if the user has it
    enabled, otherwise cascade further back to Key (or Map/Upload)."""
    if pending.get("always_show_drop", True):
        soap_df = engine.load_sheet(pending["soap_path"])
        rest_df = engine.load_sheet(pending["rest_path"])
        soap_df, rest_df = engine.normalize_namespaced_columns(
            soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
        )
        soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
        soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))
        return _render_drop_fields(
            pending_id, soap_df, rest_df, pending["key_columns"], pending["object_name"],
            ignored_fields=pending.get("ignored_fields", []),
        )
    return _retreat_to_key_or_earlier(pending_id, pending)


def _retreat_to_field_logic_or_earlier(pending_id, pending):
    """Going Back from Ready to Run: show Field Comparison Logic if the
    user has it enabled, otherwise cascade further back to Drop (or
    Key/Map/Upload). Compare-flow only -- see _advance_after_drop."""
    if pending["flow"] == "compare" and pending.get("always_show_field_logic", True):
        soap_df = engine.load_sheet(pending["soap_path"])
        rest_df = engine.load_sheet(pending["rest_path"])
        soap_df, rest_df = engine.normalize_namespaced_columns(
            soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
        )
        soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
        soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))
        return _render_field_logic(
            pending_id, soap_df, rest_df, pending["key_columns"], pending["object_name"],
            ignored_fields=pending.get("ignored_fields", []),
            field_logic=pending.get("field_logic", {}),
        )
    return _retreat_to_drop_or_earlier(pending_id, pending)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


def _run_compare(soap_df, rest_df, default_n, full_by_default, key_columns, object_name, ignored_fields=None, pending_id=None, field_logic=None):
    comparison_rows, missing_rows, column_diff, field_summary = engine.build_reports(
        soap_df, rest_df, default_n, key_columns, full_by_default, ignored_fields, field_logic
    )

    comparison_df_csv = engine.rows_to_dataframe(comparison_rows, separator=", ")
    comparison_df_csv = engine.blank_repeated_field_labels(comparison_df_csv)
    comparison_df_xlsx = engine.rows_to_dataframe(comparison_rows, separator="\n")
    missing_df = pd.DataFrame(missing_rows, columns=["Record Key", "Missing In", "Comments"])
    mismatched_df = engine.mismatched_rows_to_dataframe(comparison_rows)
    mismatched_df_csv = engine.blank_repeated_field_labels(mismatched_df)
    mismatch_summary = engine.mismatch_summary_lines(comparison_rows)

    work_dir = tempfile.mkdtemp(prefix="soap_rest_out_")
    request_id = uuid.uuid4().hex
    comparison_csv_path = os.path.join(work_dir, "field_comparison_report.csv")
    comparison_xlsx_path = os.path.join(work_dir, "field_comparison_report.xlsx")
    missing_csv_path = os.path.join(work_dir, "missing_records.csv")
    missing_xlsx_path = os.path.join(work_dir, "missing_records.xlsx")
    mismatched_csv_path = os.path.join(work_dir, "mismatched_records_report.csv")
    mismatched_xlsx_path = os.path.join(work_dir, "mismatched_records_report.xlsx")
    comparison_df_csv.to_csv(comparison_csv_path, index=False)
    engine.write_comparison_excel(comparison_df_xlsx, comparison_xlsx_path)
    missing_df.to_csv(missing_csv_path, index=False)
    engine.write_simple_excel(missing_df, missing_xlsx_path, sheet_title="Missing Records")
    mismatched_df_csv.to_csv(mismatched_csv_path, index=False)
    engine.append_lines_to_csv(mismatched_csv_path, mismatch_summary)
    engine.write_comparison_excel(mismatched_df, mismatched_xlsx_path, extra_lines=mismatch_summary)

    RUNS[request_id] = {
        "comparison_csv": comparison_csv_path,
        "comparison_xlsx": comparison_xlsx_path,
        "missing_csv": missing_csv_path,
        "missing_xlsx": missing_xlsx_path,
        "mismatched_csv": mismatched_csv_path,
        "mismatched_xlsx": mismatched_xlsx_path,
        "object_name": object_name,
    }

    total_tested = len(comparison_rows)
    mismatch_count = sum(1 for r in comparison_rows if r.tested == "Mismatch")
    verified_count = sum(1 for r in comparison_rows if r.tested == "Verified")
    unverified_count = sum(1 for r in comparison_rows if r.tested == "Unverified")

    fields_passed = sum(1 for f in field_summary.values() if f["status"] == "Passed")
    fields_failed = sum(1 for f in field_summary.values() if f["status"] == "Failed")
    fields_accepted_mismatch = sum(1 for f in field_summary.values() if f["status"] == "Accepted-Mismatch")
    fields_unverified = sum(1 for f in field_summary.values() if f["status"] == "Unverified")
    any_sample_tested = any(f["testing_size"] != "FULL" for f in field_summary.values())

    return render_template(
        "results.html",
        request_id=request_id,
        pending_id=pending_id,
        object_name=object_name,
        fields_passed=fields_passed,
        fields_failed=fields_failed,
        fields_accepted_mismatch=fields_accepted_mismatch,
        fields_unverified=fields_unverified,
        any_sample_tested=any_sample_tested,
        total_tested=total_tested,
        mismatch_count=mismatch_count,
        verified_count=verified_count,
        unverified_count=unverified_count,
        missing_count=len(missing_rows),
        only_in_soap=column_diff["only_in_soap"],
        only_in_rest=column_diff["only_in_rest"],
    )


def _run_negative(soap_df, rest_df, key_columns, object_name, ignored_fields=None, pending_id=None):
    negative_rows, column_diff = engine.build_negative_case_report(soap_df, rest_df, key_columns, ignored_fields)

    negative_df = engine.flat_rows_to_dataframe(negative_rows)
    negative_df_csv = engine.blank_repeated_field_labels(negative_df)

    work_dir = tempfile.mkdtemp(prefix="soap_rest_neg_out_")
    request_id = uuid.uuid4().hex
    negative_csv_path = os.path.join(work_dir, "negative_case_report.csv")
    negative_xlsx_path = os.path.join(work_dir, "negative_case_report.xlsx")
    negative_df_csv.to_csv(negative_csv_path, index=False)
    engine.write_comparison_excel(negative_df, negative_xlsx_path)

    RUNS[request_id] = {
        "negative_csv": negative_csv_path,
        "negative_xlsx": negative_xlsx_path,
        "object_name": object_name,
    }

    return render_template(
        "negative_results.html",
        request_id=request_id,
        pending_id=pending_id,
        object_name=object_name,
        negative_count=len(negative_rows),
        fields_affected=len({r.field_label for r in negative_rows}),
        only_in_soap=column_diff["only_in_soap"],
        only_in_rest=column_diff["only_in_rest"],
    )


@app.route("/compare", methods=["POST"])
def compare():
    soap_file = request.files.get("soap_file")
    rest_file = request.files.get("rest_file")
    object_name = request.form.get("object_name", "Object").strip() or "Object"
    try:
        default_n = int(request.form.get("default_n", "5"))
    except ValueError:
        default_n = 5
    full_by_default = request.form.get("full_by_default") == "on"
    soap_namespace = _resolve_namespace(request.form.get("soap_namespace"), engine.DEFAULT_SOAP_NAMESPACE)
    rest_namespace = _resolve_namespace(request.form.get("rest_namespace"), engine.DEFAULT_REST_NAMESPACE)
    always_show_mapping = request.form.get("always_show_mapping") == "on"
    always_show_key = request.form.get("always_show_key") == "on"
    always_show_drop = request.form.get("always_show_drop") == "on"
    always_show_field_logic = request.form.get("always_show_field_logic") == "on"
    existing_pending_id = request.form.get("existing_pending_id", "").strip()
    existing = PENDING.get(existing_pending_id) if existing_pending_id else None

    files_replaced = bool((soap_file and soap_file.filename) or (rest_file and rest_file.filename))

    if not existing and (not soap_file or not soap_file.filename or not rest_file or not rest_file.filename):
        return render_template("index.html", error="Both SOAP and REST files are required.", active_tab="compare"), 400

    work_dir = tempfile.mkdtemp(prefix="soap_rest_")
    try:
        if soap_file and soap_file.filename:
            soap_path = _save_upload(soap_file, work_dir)
        else:
            soap_path = existing["soap_path"]
        if rest_file and rest_file.filename:
            rest_path = _save_upload(rest_file, work_dir)
        else:
            rest_path = existing["rest_path"]

        soap_df = engine.load_sheet(soap_path)
        rest_df = engine.load_sheet(rest_path)
        soap_df, rest_df = engine.normalize_namespaced_columns(soap_df, rest_df, soap_namespace, rest_namespace)
        soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    except ValueError as exc:
        return render_template("index.html", error=str(exc), active_tab="compare"), 400

    pending_id = existing_pending_id if existing else uuid.uuid4().hex
    PENDING[pending_id] = {
        "flow": "compare",
        "soap_path": soap_path,
        "rest_path": rest_path,
        "object_name": object_name,
        "default_n": default_n,
        "full_by_default": full_by_default,
        "soap_namespace": soap_namespace,
        "rest_namespace": rest_namespace,
        "always_show_mapping": always_show_mapping,
        "always_show_key": always_show_key,
        "always_show_drop": always_show_drop,
        "always_show_field_logic": always_show_field_logic,
    }
    if existing and not files_replaced:
        # Editing settings on an unchanged upload -- carry forward the
        # previously chosen key/ignored fields instead of losing them.
        if "key_columns" in existing:
            PENDING[pending_id]["key_columns"] = existing["key_columns"]
        if "ignored_fields" in existing:
            PENDING[pending_id]["ignored_fields"] = existing["ignored_fields"]
        if "column_mapping" in existing:
            PENDING[pending_id]["column_mapping"] = existing["column_mapping"]
        if "field_logic" in existing:
            PENDING[pending_id]["field_logic"] = existing["field_logic"]

    # Mapping comes before key selection -- a key field itself might have
    # mismatched names across the two sheets, and mapping it first is what
    # makes it show up in the key dropdown at all. Each step is shown or
    # skipped per the Advanced options checkboxes above.
    return _advance_after_upload(pending_id, soap_df, rest_df, object_name, PENDING[pending_id])


@app.route("/negative-test", methods=["POST"])
def negative_test():
    soap_file = request.files.get("neg_soap_file")
    rest_file = request.files.get("neg_rest_file")
    object_name = request.form.get("neg_object_name", "Object").strip() or "Object"
    soap_namespace = _resolve_namespace(request.form.get("neg_soap_namespace"), engine.DEFAULT_SOAP_NAMESPACE)
    rest_namespace = _resolve_namespace(request.form.get("neg_rest_namespace"), engine.DEFAULT_REST_NAMESPACE)
    always_show_mapping = request.form.get("neg_always_show_mapping") == "on"
    always_show_key = request.form.get("neg_always_show_key") == "on"
    always_show_drop = request.form.get("neg_always_show_drop") == "on"
    existing_pending_id = request.form.get("existing_pending_id", "").strip()
    existing = PENDING.get(existing_pending_id) if existing_pending_id else None

    files_replaced = bool((soap_file and soap_file.filename) or (rest_file and rest_file.filename))

    if not existing and (not soap_file or not soap_file.filename or not rest_file or not rest_file.filename):
        return render_template("index.html", error="Both SOAP and REST files are required.", active_tab="negative"), 400

    work_dir = tempfile.mkdtemp(prefix="soap_rest_neg_")
    try:
        if soap_file and soap_file.filename:
            soap_path = _save_upload(soap_file, work_dir)
        else:
            soap_path = existing["soap_path"]
        if rest_file and rest_file.filename:
            rest_path = _save_upload(rest_file, work_dir)
        else:
            rest_path = existing["rest_path"]

        soap_df = engine.load_sheet(soap_path)
        rest_df = engine.load_sheet(rest_path)
        soap_df, rest_df = engine.normalize_namespaced_columns(soap_df, rest_df, soap_namespace, rest_namespace)
        soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    except ValueError as exc:
        return render_template("index.html", error=str(exc), active_tab="negative"), 400

    pending_id = existing_pending_id if existing else uuid.uuid4().hex
    PENDING[pending_id] = {
        "flow": "negative",
        "soap_path": soap_path,
        "rest_path": rest_path,
        "object_name": object_name,
        "soap_namespace": soap_namespace,
        "rest_namespace": rest_namespace,
        "always_show_mapping": always_show_mapping,
        "always_show_key": always_show_key,
        "always_show_drop": always_show_drop,
    }
    if existing and not files_replaced:
        if "key_columns" in existing:
            PENDING[pending_id]["key_columns"] = existing["key_columns"]
        if "ignored_fields" in existing:
            PENDING[pending_id]["ignored_fields"] = existing["ignored_fields"]
        if "column_mapping" in existing:
            PENDING[pending_id]["column_mapping"] = existing["column_mapping"]
        if "field_logic" in existing:
            PENDING[pending_id]["field_logic"] = existing["field_logic"]

    return _advance_after_upload(pending_id, soap_df, rest_df, object_name, PENDING[pending_id])


@app.route("/confirm-key", methods=["POST"])
def confirm_key():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    key_columns = [c for c in request.form.getlist("key_columns") if c]
    key_columns = list(dict.fromkeys(key_columns))  # de-dupe, preserve order

    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))

    if not key_columns:
        return _render_select_key(
            pending_id, soap_df, rest_df, pending["object_name"], selected_keys=key_columns,
            error="Select at least one field to use as the matching key.",
        )

    pending["key_columns"] = key_columns
    return _advance_after_key(pending_id, soap_df, rest_df, pending["object_name"], pending)


@app.route("/back-to-key", methods=["POST"])
def back_to_key():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    try:
        dropped_fields = json.loads(request.form.get("dropped_fields_json", "[]"))
        if not isinstance(dropped_fields, list):
            dropped_fields = []
    except (TypeError, ValueError):
        dropped_fields = []
    pending["ignored_fields"] = dropped_fields

    return _retreat_to_key_or_earlier(pending_id, pending)


@app.route("/confirm-mapping", methods=["POST"])
def confirm_mapping():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    try:
        mapping = json.loads(request.form.get("mapping_json", "{}"))
        if not isinstance(mapping, dict):
            mapping = {}
    except (TypeError, ValueError):
        mapping = {}
    pending["column_mapping"] = mapping

    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)

    return _advance_after_mapping(pending_id, soap_df, rest_df, pending["object_name"], pending)


@app.route("/back-to-mapping", methods=["POST"])
def back_to_mapping():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    return _retreat_to_mapping_or_earlier(pending_id, pending)


@app.route("/back-to-upload", methods=["POST"])
def back_to_upload():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    return _render_upload_with_prefill(pending_id, pending)


@app.route("/run", methods=["POST"])
def run_pending():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    ignored_fields = pending.get("ignored_fields", [])

    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))
    key_columns = pending["key_columns"]

    if pending["flow"] == "negative":
        return _run_negative(soap_df, rest_df, key_columns, pending["object_name"], ignored_fields, pending_id)

    return _run_compare(
        soap_df, rest_df, pending["default_n"], pending["full_by_default"], key_columns,
        pending["object_name"], ignored_fields, pending_id, pending.get("field_logic", {}),
    )


@app.route("/back-to-options", methods=["POST"])
def back_to_options():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    return _render_options(
        pending_id, pending["object_name"], pending["flow"],
        ignored_fields=pending.get("ignored_fields", []),
    )


@app.route("/confirm-drop", methods=["POST"])
def confirm_drop():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    try:
        dropped_fields = json.loads(request.form.get("dropped_fields_json", "[]"))
        if not isinstance(dropped_fields, list):
            dropped_fields = []
    except (TypeError, ValueError):
        dropped_fields = []
    pending["ignored_fields"] = dropped_fields

    soap_df = engine.load_sheet(pending["soap_path"])
    rest_df = engine.load_sheet(pending["rest_path"])
    soap_df, rest_df = engine.normalize_namespaced_columns(
        soap_df, rest_df, pending["soap_namespace"], pending["rest_namespace"]
    )
    soap_df, rest_df = engine.normalize_key_column_names(soap_df, rest_df)
    soap_df, rest_df = engine.apply_column_mapping(soap_df, rest_df, pending.get("column_mapping", {}))

    return _advance_after_drop(pending_id, soap_df, rest_df, pending["object_name"], pending)


@app.route("/confirm-field-logic", methods=["POST"])
def confirm_field_logic():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    try:
        field_logic = json.loads(request.form.get("field_logic_json", "{}"))
        if not isinstance(field_logic, dict):
            field_logic = {}
    except (TypeError, ValueError):
        field_logic = {}
    pending["field_logic"] = field_logic

    return _render_options(pending_id, pending["object_name"], pending["flow"], ignored_fields=pending.get("ignored_fields", []))


@app.route("/back-to-field-logic", methods=["POST"])
def back_to_field_logic():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    return _retreat_to_field_logic_or_earlier(pending_id, pending)


@app.route("/back-to-drop", methods=["POST"])
def back_to_drop():
    pending_id = request.form.get("pending_id", "")
    pending = PENDING.get(pending_id)
    if not pending:
        abort(404)

    return _retreat_to_drop_or_earlier(pending_id, pending)


DOWNLOAD_SUFFIXES = {
    "comparison_csv": "field_comparison_report.csv",
    "comparison_xlsx": "field_comparison_report.xlsx",
    "missing_csv": "missing_records.csv",
    "missing_xlsx": "missing_records.xlsx",
    "mismatched_csv": "detailed_mismatched_records_report.csv",
    "mismatched_xlsx": "detailed_mismatched_records_report.xlsx",
    "negative_csv": "negative_case_report.csv",
    "negative_xlsx": "negative_case_report.xlsx",
}


@app.route("/download/<request_id>/<which>", methods=["GET"])
def download(request_id, which):
    run = RUNS.get(request_id)
    if not run or which not in DOWNLOAD_SUFFIXES:
        abort(404)
    object_slug = _safe_filename_part(run["object_name"])
    download_name = f"{object_slug}_{DOWNLOAD_SUFFIXES[which]}"
    return send_file(run[which], as_attachment=True, download_name=download_name)


if __name__ == "__main__":
    app.run(debug=False)
