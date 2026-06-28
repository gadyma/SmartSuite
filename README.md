# SmartSuite Tools

Three Python programs for automating SmartSuite backups, permission audits, and Google Sheet imports. All share a single configuration file — `SmartsuiteConfig.py`.

---

## Prerequisites

Install required Python packages:

```bash
python3 -m pip install requests google-api-python-client google-auth google-auth-httplib2
```

---

## Configuration (`SmartsuiteConfig.py`)

All three programs require a `--config` argument pointing to `SmartsuiteConfig.py`. The file supports two approaches:

### Option A — AWS Secrets Manager (recommended for production)

The machine must have an IAM role with `secretsmanager:GetSecretValue` permission. The secret should be a JSON object with the keys below:

```python
import json, boto3
from botocore.exceptions import ClientError

def get_aws_secret(secret_name, region_name="il-central-1"):
    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])

SECRET_ID = "secrets/prod/myapp/config"
REGION    = "il-central-1"

secrets = get_aws_secret(secret_name=SECRET_ID, region_name=REGION)

TOKEN                        = secrets.get("smartsuite_api_key")
ACCOUNT_ID                   = secrets.get("smartsuite_workspace")
GOOGLE_SERVICE_ACCOUNT_JSON  = secrets.get("google_service_vaules_json")
GOOGLE_SHARED_DRIVE_ID       = secrets.get("GOOGLE_SHARED_DRIVE_ID")
GOOGLE_BACKUP_ROOT_FOLDER_ID = secrets.get("GOOGLE_BACKUP_ROOT_FOLDER_ID")
GOOGLE_SHEET_PERMISSIONS_ID  = secrets.get("GOOGLE_SHEET_PERMISSIONS_ID")
```

### Option B — Direct values (local / development)

```python
import os

# SmartSuite API
TOKEN      = "your-smartsuite-api-token"
ACCOUNT_ID = "your-account-id"  # first segment of the URL: app.smartsuite.com/ACCOUNTID/...

# Google service account — either a path to a JSON key file or the JSON content as a string
GOOGLE_SERVICE_ACCOUNT_FILE  = os.path.expanduser("~/service.json")

# Google Drive
GOOGLE_SHARED_DRIVE_ID       = "your-shared-drive-id"
GOOGLE_BACKUP_ROOT_FOLDER_ID = "your-backup-root-folder-id"

# Google Sheet for permissions output
GOOGLE_SHEET_PERMISSIONS_ID  = "your-permissions-spreadsheet-id"
```

**Getting a SmartSuite API token:** see [SmartSuite docs](https://help.smartsuite.com/en/articles/4855681-generating-an-api-key).

**Google service account:** create a service account in Google Cloud Console, grant it Editor access to your shared Drive and the permissions spreadsheet, and download the JSON key.

---

## Script 1 — `Backup_SmartSuite.py`

### What it does

Performs a full weekly backup of all SmartSuite data to Google Drive:

- Fetches every solution (workspace) and all tables within them
- Exports each table as a CSV with an injected **Record ID** column
- Saves each table's field structure as a JSON file
- Uploads file attachments to Drive (deduplicated — each attachment is only uploaded once, identified by its handle)
- Maintains a **dated backup folder** per run (e.g. `2025-05-25 10:00`) inside the backup root
- Keeps a **Latest** folder with one Google Spreadsheet per solution, one tab per table — always reflecting the most recent data
- **Rotates old backups**, keeping only the 7 most recent dated folders
- Uses a **local lock file** (`~/.smartsuite_last_backup`) to prevent duplicate runs on the same day
- Aborts cleanly if there is no network connectivity (VPN down, etc.)

### Folder structure on Drive

```
Backup Root/
├── 2025-05-25 10:00/        ← dated snapshot
│   ├── SolutionName/
│   │   ├── TableName.csv
│   │   └── TableName.structure.json
│   └── ...
├── Latest/
│   ├── SolutionName         ← Google Spreadsheet, one tab per table
│   └── ...
├── Attachments/
│   └── SolutionName/
│       └── TableName/
│           └── <handle>/filename
└── Meta/
    ├── solutions.json
    └── tables.json
```

### Usage

```bash
python3 Backup_SmartSuite.py --config ~/SmartsuiteConfig.py
```

### Cron example (daily at 10:00)

```cron
0 10 * * * /usr/local/bin/python3 "$HOME/git/SmartsuiteBackup/Backup_SmartSuite.py" --config "$HOME/SmartsuiteConfig.py" >> "$HOME/logs/SScron.log" 2>&1
```

---

## Script 2 — `Smartsuite_Permissions_Audit.py`

### What it does

Audits and exports all SmartSuite permission settings to a Google Sheet and a local CSV:

- Fetches all **solutions** and their access level, members, teams, owners, and private settings
- Fetches all **tables** and their access level, members, and teams
- Fetches **field-level permissions** (read/write audience, specific members and teams) for every field in every table
- Resolves all internal user IDs and team IDs to human-readable names (full name or email)
- Writes output to:
  - A **Google Sheet** (clears and rewrites on every run)
  - A **local CSV** at `DEST_FOLDERPERM/permissions.csv` as a fallback

### Output columns

| Column | Description |
|---|---|
| Type | `solution`, `Table`, or `field` |
| Solution | Solution (workspace) name |
| Table | Table name (empty for solution rows) |
| Field | Field name (only for field rows) |
| level | Permission level (e.g. `all_members`, `specific`) |
| members | Members with access |
| teams | Teams with access |
| members_read | Members with read access (field rows) |
| members_write | Members with write access (field rows) |
| teams_read | Teams with read access (field rows) |
| teams_write | Teams with write access (field rows) |
| owners | Owners |
| private_to | Private access setting |
| level_read | Read audience (field rows) |
| level_write | Write audience (field rows) |

### Usage

```bash
python3 Smartsuite_Permissions_Audit.py --config ~/SmartsuiteConfig.py
```

### Cron example (Mondays at 10:10)

```cron
10 10 * * 1 /usr/local/bin/python3 "$HOME/git/SmartsuiteBackup/Smartsuite_Permissions_Audit.py" --config "$HOME/SmartsuiteConfig.py" >> "$HOME/logs/SScron.log" 2>&1
```

---

## Script 3 — `ImportSheet/Sync_Sheet_to_SmartSuite.py`

### What it does

Syncs rows from a Google Sheet into SmartSuite, creating or updating records based on a configurable key field:

- Reads sync job definitions from `sync_jobs.py` — each job maps a Google Sheet tab to a SmartSuite table
- Fetches all existing SmartSuite records and indexes them by key field
- Reads rows from the source Google Sheet, converts data types (dates, numbers, text), and for each row:
  - **Creates** a new SmartSuite record if the key value does not exist
  - **Updates** the existing record if it does
- Optionally posts a summary notification to a Slack webhook (`WEBHOOK_URL` in a local `config.py`)
- Retries failed API calls with exponential back-off

### Usage

```bash
python3 ImportSheet/Sync_Sheet_to_SmartSuite.py --config ~/SmartsuiteConfig.py --jobs ~/sync_jobs.py
```

Add `--dry-run` to log what would change without writing anything to SmartSuite.

### Cron example (daily at 08:00)

```cron
0 8 * * * /usr/local/bin/python3 "$HOME/git/SmartsuiteBackup/ImportSheet/Sync_Sheet_to_SmartSuite.py" --config "$HOME/SmartsuiteConfig.py" --jobs "$HOME/sync_jobs.py" >> "$HOME/logs/SScron.log" 2>&1
```
