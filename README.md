# ALPR Audit Kit

A local-first prototype for screening Flock Safety ALPR audit workbooks for records that may need closer human review. It does not decide whether a search was improper and does not use an AI service.

> **Status:** early command-line prototype. A point-and-click app is planned; this repository is not yet a one-click tool for nontechnical users.

## What it does

- Reads `.xlsx` audit workbooks without changing the original files.
- Imports rows in bounded batches to local Parquet files and screens them with DuckDB.
- Produces candidate lists and summaries for review, including query-reason wording, missing or placeholder purpose fields, high-volume bursts, repeat searches, broad search windows, event access, hotlists, and sharing.
- Preserves workbook, sheet, and Excel-row references so a reviewer can return to the source record.

Flags are leads for follow-up, not findings of misconduct. A keyword, blank field, high count, or network scope does not by itself establish an unauthorized search, a returned plate hit, disclosure, or a recipient. Read [the screening methods and limitations](docs/METHODS.md) before interpreting results.

## Privacy

The scanner runs locally and contains no code to upload workbook contents. The results can still contain sensitive account, case, or plate-related information. Keep source workbooks and generated results off public repositories and shared drives unless they have been reviewed and safely redacted. The `.gitignore` blocks common workbook and output formats as a safeguard; do not rely on it instead of checking `git status` before publishing.

See [privacy notes](docs/PRIVACY.md). No real agency data is included in this repository.

## Current quick start

This prototype requires Python 3.12 and a command line. It is not yet the promised point-and-click workflow.

1. Install Python 3.12.
2. From this repository folder, create an environment and install the pinned dependencies:

   **Windows PowerShell**
   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   **macOS or Linux**
   ```sh
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. Create a folder named `flock_audit_logs` beside `scripts/` and put the downloaded `.xlsx` audit workbooks in it. Use filenames containing one of these workbook labels: `NETWORK AUDITS`, `ORGANIZATION AUDITS`, `EVENT LOG`, `HOTLIST`, or `SharedNetworks`. You can pass another input folder with `--input-dir`.
4. Import the workbooks:

   **Windows PowerShell**
   ```powershell
   .venv\Scripts\python.exe scripts\alpr_screen.py ingest
   ```

   **macOS or Linux**
   ```sh
   .venv/bin/python scripts/alpr_screen.py ingest
   ```

5. Run the screening analysis:

   **Windows PowerShell**
   ```powershell
   .venv\Scripts\python.exe scripts\alpr_analyze.py
   ```

   **macOS or Linux**
   ```sh
   .venv/bin/python scripts/alpr_analyze.py
   ```

The importer caches completed files by SHA-256 and parser version. It stops if it finds no workbooks. Do not use `--partial` results as a complete analysis; the default analysis refuses an incomplete import.

## Outputs

- `analysis/alpr/data/`: imported Parquet rows and workbook manifests.
- `analysis/alpr/results/`: CSV review lists, summaries, and run status.

These directories are ignored by Git because their contents are derived from user-supplied records and should remain private. Check coverage, import warnings, reconciliation, and `run.json` before relying on a result. See [methods](docs/METHODS.md).

## Roadmap

1. Plain-language records request and download guides.
2. Workbook detection and a guided point-and-click interface.
3. One-click local desktop releases, starting with Windows.
4. Synthetic sample workbooks and usability review with nontechnical users.

Contributions to the prototype and its documentation are welcome. The software is MIT licensed; records and datasets supplied by users are not part of the license or distribution.
