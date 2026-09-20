# Graph Report - soap-rest-data-comparision  (2026-09-20)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 133 nodes · 302 edges · 11 communities (9 shown, 2 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `cc1ae7ba`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9

## God Nodes (most connected - your core abstractions)
1. `normalize_key_column_names()` - 14 edges
2. `normalize_namespaced_columns()` - 14 edges
3. `load_sheet()` - 14 edges
4. `_retreat_to_key_or_earlier()` - 11 edges
5. `_retreat_to_mapping_or_earlier()` - 10 edges
6. `_run_compare()` - 10 edges
7. `_retreat_to_drop_or_earlier()` - 10 edges
8. `apply_column_mapping()` - 10 edges
9. `_classify()` - 9 edges
10. `diff_columns()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `_run_compare()` --calls--> `build_reports()`  [EXTRACTED]
  app.py → comparison_engine.py
- `_run_compare()` --calls--> `rows_to_dataframe()`  [EXTRACTED]
  app.py → comparison_engine.py
- `compare()` --calls--> `load_sheet()`  [EXTRACTED]
  app.py → comparison_engine.py
- `negative_test()` --calls--> `load_sheet()`  [EXTRACTED]
  app.py → comparison_engine.py
- `_retreat_to_mapping_or_earlier()` --calls--> `load_sheet()`  [EXTRACTED]
  app.py → comparison_engine.py

## Import Cycles
- None detected.

## Communities (11 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.10
Nodes (32): build_reports(), _classify(), _classify_case_insensitive(), _classify_date(), _classify_number(), _classify_picklist(), compare_field(), _compare_field_full() (+24 more)

### Community 1 - "Community 1"
Cohesion: 0.18
Nodes (17): _advance_after_upload(), back_to_mapping(), compare(), negative_test(), Entry point right after upload -- decides whether to show Map Unmatched Fields…, Going Back from Select Matching Key: show Map Unmatched Fields if it would…, _render_map_columns(), _resolve_namespace() (+9 more)

### Community 2 - "Community 2"
Cohesion: 0.13
Nodes (17): _run_compare(), _run_negative(), run_pending(), append_lines_to_csv(), blank_repeated_field_labels(), flat_rows_to_dataframe(), mismatch_summary_lines(), mismatched_rows_to_dataframe() (+9 more)

### Community 3 - "Community 3"
Cohesion: 0.17
Nodes (17): build_negative_case_report(), _composite_key_series(), _find_default_key_column(), load_sheet(), match_records(), _normalize_header(), Finds a column matching a known default-key alias regardless of…, Returns (soap_column_name, rest_column_name) if both sheets have a recognizable… (+9 more)

### Community 4 - "Community 4"
Cohesion: 0.20
Nodes (11): back_to_upload(), download(), _render_upload_with_prefill(), _safe_filename_part(), flask, json, os, pandas (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.20
Nodes (11): _advance_after_key(), _advance_after_mapping(), confirm_key(), confirm_mapping(), Called once mapping is settled (either just confirmed, or skipped because it…, Called once the matching key is settled -- decides whether to show Drop Fields…, _render_drop_fields(), _render_select_key() (+3 more)

### Community 6 - "Community 6"
Cohesion: 0.25
Nodes (9): back_to_drop(), back_to_field_logic(), Going Back from Ready to Run: show Drop Fields if the user has it enabled,…, Going Back from Ready to Run: show Field Comparison Logic if the user has it…, _render_field_logic(), _retreat_to_drop_or_earlier(), _retreat_to_field_logic_or_earlier(), apply_column_mapping() (+1 more)

### Community 7 - "Community 7"
Cohesion: 0.32
Nodes (8): _advance_after_drop(), back_to_options(), confirm_drop(), confirm_field_logic(), index(), Called once dropped fields are settled -- decides whether to show Field…, _render_options(), route

### Community 8 - "Community 8"
Cohesion: 0.67
Nodes (3): back_to_key(), Going Back from Drop Fields: show Select Matching Key if it would actually have…, _retreat_to_key_or_earlier()

## Knowledge Gaps
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `normalize_namespaced_columns()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Why does `normalize_key_column_names()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Why does `load_sheet()` connect `Community 3` to `Community 0`, `Community 1`, `Community 2`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.10416666666666667 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.1323529411764706 - nodes in this community are weakly interconnected._