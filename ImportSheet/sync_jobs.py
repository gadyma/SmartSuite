# SAMPLE — copy this file to ~/sync_jobs.py and fill in real values.
#
# Each entry in JOBS defines one Google Sheets → SmartSuite sync.
# Fields:
#   name        : human-readable label for logs / Slack notifications
#   sheet_id    : Google Spreadsheet ID (the long string in the URL)
#   tab_name    : sheet tab name to read
#   app_id      : SmartSuite application (table) ID  ← use smartsuite_discover.py to find
#   key_column  : column header in the sheet that identifies the SmartSuite record
#   key_field   : SmartSuite field slug to match the key against (e.g. "autonumber")
#   field_map   : (optional) {sheet_column_header: smartsuite_field_name}
#                 omit entirely to auto-match sheet headers to SmartSuite field names
#   select_maps : (optional) override auto-fetched select options {field_name: {label: value_code}}
#   dry_run     : (optional) True → force dry-run for this job regardless of CLI flag

JOBS = [
    {
        "name":       "My Sync Job",
        "sheet_id":   "<google-spreadsheet-id>",
        "tab_name":   "<tab name>",
        "app_id":     "<smartsuite-app-id>",
        "key_column": "<sheet column used as key>",
        "key_field":  "autonumber",
        # field_map omitted → auto-match sheet column headers to SmartSuite field names
        # OR specify explicit column-to-field mapping:
        "field_map": {
            "<sheet column header>": "<SmartSuite field name>",
        },
        # select_maps auto-fetched — only add to override specific options
    },
]
