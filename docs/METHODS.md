# Screening methods and limitations

This prototype uses deterministic regular expressions, field checks, and count thresholds. It does not use an AI model or make legal or misconduct determinations.

## Candidate groups

The analysis searches query reasons for wording about personal or relationship matters, tests/training/demos, immigration, outside requesters, protests/rallies/demonstrations, placeholder reasons, and blank purpose fields. Matching is case-insensitive. The patterns are visible in `scripts/alpr_analyze.py`; they can miss euphemisms and unusual spellings.

Other screens include:

- bursts of at least 20 queries for one organization and account in one UTC minute;
- at least 20 searches of the same readable plate by the same organization and account across at least three UTC dates;
- long or reversed requested time windows;
- broad network-count examples, event access, hotlist observations, and sharing records.

Thresholds are screening choices, not legal or policy rules. Redacted and blank identifiers limit recurrence analysis. Rows with hidden plate values are not treated as a common vehicle.

## What logs can and cannot show

An audit row may show that a query was submitted, the account label, a reason, a requested time window, or a network count. These fields may not establish who was using a shared account, the underlying purpose, which cameras were selected, whether any plate matched, whether images were returned, what was exported, or who ultimately received information.

A blank or placeholder field is a reason to ask for context, not proof of abuse. A high network count describes search scope, not successful hits. A reason mentioning a protest does not alone show that a person was monitored because of lawful activity.

Use source workbook, sheet, and row references to seek associated policies, case or dispatch records, operator/session records, query results, exports, and access/disclosure history. A records request cannot guarantee that an agency retained or will release those materials.

## Time and version handling

The importer converts Excel date serials and preserves source field text. The analysis parses UTC timestamps explicitly. Some source files may have shifted columns or incomplete dates; inspect `layout_anomalies.csv` and coverage summaries.

Version handling is not yet generic. The analyzer's baseline selection excludes three original 2026 workbook filenames from the development dataset; it does not currently determine whether the matching revised `(1)` files are present. The 2026 reconciliation logic is likewise tied to those source export names. Before screening another release, review which files enter `base` and whether every old/revised pair is reconciled. Reconciliation does not prove an agency export is complete.
