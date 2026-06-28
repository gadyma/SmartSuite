# SmartSuite Sync Tools

Scripts for syncing data from Google Sheets into SmartSuite and for discovering SmartSuite app/field IDs.

**Used by:** Risk & Compliance team to push EOS (End of Service) status updates and findings from working Google Sheets into SmartSuite without manual copy-paste.

---

## Files

| File | Purpose |
|---|---|
| `Sync_Sheet_to_SmartSuite.py` | Main sync engine — reads Google Sheets, compares with SmartSuite, patches changed records |
| `smartsuite_discover.py` | Helper — browse app IDs and field names without touching the API manually |
| `sync_jobs.py` | Sample template — copy to `~/sync_jobs.py` and fill in real values |
| `~/sync_jobs.py` | **Your config** — the live jobs file with real IDs (not committed to the repo) |
| `~/SmartsuiteConfig.py` | Credentials — SmartSuite API token, account ID, Google service account file path |

---

## Prerequisites

```bash
python3 -m pip install requests google-api-python-client google-auth google-auth-httplib2
```

---

## Setup

### 1. SmartsuiteConfig.py (credentials)

Create `~/SmartsuiteConfig.py`:

```python
TOKEN                    = "your-smartsuite-api-token"
ACCOUNT_ID               = "your-account-id"
GOOGLE_SERVICE_ACCOUNT_FILE = "/path/to/service-account.json"
```

- **TOKEN / ACCOUNT\_ID** — SmartSuite → Settings → API
- **GOOGLE\_SERVICE\_ACCOUNT\_FILE** — Google Cloud Console → IAM → Service Accounts → create a key (JSON), then share the target spreadsheets with the service account email

### 2. Slack notifications (optional)

Create `~/config.py` (or `config.py` next to the script):

```python
WEBHOOK_URL = "https://hooks.slack.com/services/..."
```

Notifications are sent only when records are actually updated or errors occur.

### 3. sync_jobs.py (job definitions)

Copy the sample from the repo and fill in real values:

```bash
cp sync_jobs.py ~/sync_jobs.py
```

`~/sync_jobs.py` is the only file you edit day-to-day. Each entry in `JOBS` describes one sync:

```python
JOBS = [
    {
        "name":       "EOS Updates",           # label shown in logs and Slack
        "sheet_id":   "<google-spreadsheet-id>",
        "tab_name":   "Sheet1",                # tab name within the spreadsheet
        "app_id":     "<smartsuite-app-id>",
        "key_column": "Auto number",           # sheet column used to match records
        "key_field":  "autonumber",            # SmartSuite field slug for the same key

        # field_map: {sheet column header → SmartSuite field name}
        # Omit entirely to auto-match by column name.
        "field_map": {
            "Sheet Column": "SmartSuite Field Name",
        },

        # select_maps: optional — auto-fetched from the API.
        # Add only to override a specific option label.
        # "select_maps": { "SmartSuite Field Name": {"Label": "value_code"} },
    },
]
```

---

## Running

Both `--config` and `--jobs` are required:

```bash
# Live sync
python3 Sync_Sheet_to_SmartSuite.py --config ~/SmartsuiteConfig.py --jobs ~/sync_jobs.py

# Dry run — shows what would change, writes nothing
python3 Sync_Sheet_to_SmartSuite.py --config ~/SmartsuiteConfig.py --jobs ~/sync_jobs.py --dry-run

# Use a different jobs file
python3 Sync_Sheet_to_SmartSuite.py --config ~/SmartsuiteConfig.py --jobs /path/to/other_jobs.py
```

---

## Adding a new sync job

### Step 1 — Find the app ID

```bash
python3 smartsuite_discover.py --config ~/SmartsuiteConfig.py
# Filter by name if the list is long:
python3 smartsuite_discover.py --config ~/SmartsuiteConfig.py --search Findings
```

### Step 2 — Find the field names

```bash
python3 smartsuite_discover.py --config ~/SmartsuiteConfig.py <app_id>
```

This prints every field name (use these as `field_map` values) and, at the bottom, a paste-ready `field_map` block.

### Step 3 — Add the job to ~/sync_jobs.py

Copy the template from the comments in `~/sync_jobs.py`, fill in the values from steps 1–2, and test with `--dry-run`:

```bash
python3 Sync_Sheet_to_SmartSuite.py --config ~/SmartsuiteConfig.py --jobs ~/sync_jobs.py --dry-run
```

Verify the output looks correct, then run without `--dry-run`.

---

## How the sync works

1. Reads all rows from the specified Google Sheet tab
2. Fetches all records from the SmartSuite app (paginated, up to 1 000 per page)
3. For each sheet row, looks up the matching SmartSuite record by the key field
4. Compares each mapped field — only fields that actually changed are included in the PATCH
5. Sends a Slack notification summarising updates and errors (if a webhook is configured)

Select-field option codes (`"On Track"` → internal code) are fetched automatically from the SmartSuite API at runtime — no manual lookup needed.
