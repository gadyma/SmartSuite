"""SmartSuite + Google credentials loader.

Priority (first wins):
  1. Local secrets file — path from SMARTSUITE_SECRETS_FILE env var,
     or ~/smartsuite_secrets.json by default.
  2. AWS Secrets Manager — secret 'secrets/prod/esh-automation/cron/config'
     in il-central-1, using the EC2 IAM role (no credentials needed on EC2).

To use locally: export the secret to ~/smartsuite_secrets.json once:
    python3 -c "
    import boto3, json
    s = boto3.client('secretsmanager', region_name='il-central-1')
    print(s.get_secret_value(SecretId='secrets/prod/esh-automation/cron/config')['SecretString'])
    " > ~/smartsuite_secrets.json
"""

import json
import os
from pathlib import Path

_LOCAL_FILE = Path(os.environ.get("SMARTSUITE_SECRETS_FILE", Path.home() / "smartsuite_secrets.json"))

if _LOCAL_FILE.exists():
    secrets = json.loads(_LOCAL_FILE.read_text())
    print(f"[SmartsuiteConfig] local secrets: {_LOCAL_FILE}", flush=True)
else:
    import boto3
    client = boto3.client("secretsmanager", region_name="il-central-1")
    secrets = json.loads(client.get_secret_value(
        SecretId="secrets/prod/esh-automation/cron/config"
    )["SecretString"])
    print("[SmartsuiteConfig] AWS Secrets Manager", flush=True)

TOKEN      = secrets["smartsuite_api_key"]
ACCOUNT_ID = secrets["smartsuite_workspace"]

# Google credentials as a dict (from secrets) — use from_service_account_info(), not from_service_account_file()
GOOGLE_SERVICE_ACCOUNT_INFO  = secrets["google_service_vaules_json"]
GOOGLE_SHARED_DRIVE_ID       = secrets["GOOGLE_SHARED_DRIVE_ID"]
GOOGLE_BACKUP_ROOT_FOLDER_ID = secrets["GOOGLE_BACKUP_ROOT_FOLDER_ID"]
GOOGLE_SHEET_PERMISSIONS_ID  = secrets["GOOGLE_SHEET_PERMISSIONS_ID"]
