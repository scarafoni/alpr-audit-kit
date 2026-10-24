# Privacy and safe handling

Public-records exports can contain plate identifiers, search details, user or account labels, case numbers, and other sensitive information. Treat source workbooks and all row-level output as restricted even if some fields are redacted.

- Keep source workbooks in the ignored `flock_audit_logs/` folder.
- Keep imported Parquet files and results in ignored `analysis/alpr/`.
- Do not commit source records, row-level results, screenshots of private request portals, credentials, security keys, or email correspondence.
- Before sharing a report, review it for identifiers and remove any fields that do not need to be public.
- The scanner runs on the user's computer and has no workbook upload or telemetry feature. Installing dependencies requires downloading packages from PyPI; the workbook analysis itself does not call an external service.
- The output can still identify accounts, cases, or searches. Local processing is not a guarantee that the computer or its backups are secure.

If a file was accidentally committed, deleting it in a later commit does not remove it from Git history. Revoke any exposed credentials and follow GitHub's sensitive-data removal guidance.
