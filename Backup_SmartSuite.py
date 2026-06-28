# Installation prerequisites:
# python3 -m pip install requests google-api-python-client google-auth
# Version 4.0 - Full Drive API: dated backups + attachments on Drive, local temp staging only

import sys
sys.stdout.reconfigure(line_buffering=True)
import argparse, importlib.util
import requests
import json
import os
import csv
import io
import tempfile
import time
import threading
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import shutil
import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ================= CONFIG IMPORT =================
def load_config(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("SmartsuiteConfig", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

parser = argparse.ArgumentParser(description="Backup SmartSuite to Google Drive")
parser.add_argument("--config", required=True, metavar="PATH", help="Path to SmartsuiteConfig.py")
args = parser.parse_args()
cfg = load_config(args.config)

TOKEN                      = cfg.TOKEN
ACCOUNT_ID                 = cfg.ACCOUNT_ID
GOOGLE_SERVICE_ACCOUNT_FILE  = cfg.GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHARED_DRIVE_ID       = cfg.GOOGLE_SHARED_DRIVE_ID
GOOGLE_BACKUP_ROOT_FOLDER_ID = cfg.GOOGLE_BACKUP_ROOT_FOLDER_ID

# ================= SETTINGS =================
GENERATIONS = 7
TIMEOUT = 60
MAX_SOLUTIONS = None  # Set to an integer (e.g. 3) for debug runs; None = backup all
FOLDER_MIME = 'application/vnd.google-apps.folder'
SHEET_MIME  = 'application/vnd.google-apps.spreadsheet'
RUN_LOCK_FILE = Path.home() / '.smartsuite_last_backup'  # local date lock, avoids Drive delete permissions

timestamp    = datetime.now().strftime("%Y-%m-%d %H:%M")
current_date = timestamp.split()[0]

BASE_URL = "https://app.smartsuite.com/api/v1"
ss_headers = {
    "accept": "application/json",
    "Authorization": "Token " + TOKEN,
    "Content-Type": "application/json",
    "ACCOUNT-ID": ACCOUNT_ID
}

# ================= GOOGLE DRIVE =================
_creds = service_account.Credentials.from_service_account_file(
    GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/spreadsheets']
)
# google_auth_httplib2.AuthorizedHttp wraps httplib2 with credentials and enforces timeout
_http = google_auth_httplib2.AuthorizedHttp(_creds, http=httplib2.Http(timeout=TIMEOUT))
drive_svc  = build('drive',  'v3', http=_http)
sheets_svc = build('sheets', 'v4', http=_http)

def _q(name):
    return name.replace("'", "\\'")

_folder_cache = {}
_solution_sheet_id = {}  # sol_name -> spreadsheet_id for consolidated Latest sheets
_thread_local = threading.local()

def _thread_drive_svc():
    if not hasattr(_thread_local, 'svc'):
        http = google_auth_httplib2.AuthorizedHttp(_creds, http=httplib2.Http(timeout=TIMEOUT))
        _thread_local.svc = build('drive', 'v3', http=http)
    return _thread_local.svc

def drive_list(q, fields='files(id,name)'):
    res = drive_svc.files().list(
        q=q + " and trashed=false",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        corpora='drive', driveId=GOOGLE_SHARED_DRIVE_ID,
        fields=fields
    ).execute()
    return res.get('files', [])

def get_or_create_folder(name, parent_id):
    key = (name, parent_id)
    if key in _folder_cache:
        return _folder_cache[key]
    files = drive_list(f"name='{_q(name)}' and '{parent_id}' in parents and mimeType='{FOLDER_MIME}'")
    if files:
        fid = files[0]['id']
    else:
        fid = drive_svc.files().create(
            body={'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]},
            supportsAllDrives=True, fields='id'
        ).execute()['id']
    _folder_cache[key] = fid
    return fid

def upload_file(name, content_bytes, mimetype, parent_id):
    """Upload or overwrite a file in a Drive folder."""
    files = drive_list(f"name='{_q(name)}' and '{parent_id}' in parents and mimeType!='{FOLDER_MIME}'")
    media = MediaInMemoryUpload(content_bytes, mimetype=mimetype)
    if files:
        drive_svc.files().update(
            fileId=files[0]['id'], media_body=media, supportsAllDrives=True
        ).execute()
    else:
        drive_svc.files().create(
            body={'name': name, 'parents': [parent_id]},
            media_body=media, supportsAllDrives=True, fields='id'
        ).execute()

def upsert_sheet(name, csv_content, parent_id):
    """Create or update a Google Sheet in Drive."""
    files = drive_list(f"name='{_q(name)}' and '{parent_id}' in parents and mimeType='{SHEET_MIME}'")
    if files:
        sid = files[0]['id']
        values = list(csv.reader(io.StringIO(csv_content)))
        sheets_svc.spreadsheets().values().clear(spreadsheetId=sid, range='A:ZZZ').execute()
        sheets_svc.spreadsheets().values().update(
            spreadsheetId=sid, range='A1',
            valueInputOption='RAW', body={'values': values}
        ).execute()
    else:
        media = MediaInMemoryUpload(csv_content.encode('utf-8'), mimetype='text/csv')
        drive_svc.files().create(
            body={'name': name, 'mimeType': SHEET_MIME, 'parents': [parent_id]},
            media_body=media, supportsAllDrives=True, fields='id'
        ).execute()

def _get_or_create_solution_spreadsheet(sol_name, parent_id):
    """Return the spreadsheet ID for a solution's consolidated Latest sheet."""
    if sol_name in _solution_sheet_id:
        return _solution_sheet_id[sol_name]
    files = drive_list(f"name='{_q(sol_name)}' and '{parent_id}' in parents and mimeType='{SHEET_MIME}'")
    if files:
        sid = files[0]['id']
    else:
        sid = drive_svc.files().create(
            body={'name': sol_name, 'mimeType': SHEET_MIME, 'parents': [parent_id]},
            supportsAllDrives=True, fields='id'
        ).execute()['id']
    _solution_sheet_id[sol_name] = sid
    return sid

def upsert_tab(spreadsheet_id, tab_name, csv_content):
    """Create or overwrite a tab in an existing spreadsheet."""
    ss_meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s['properties']['title']: s['properties']['sheetId'] for s in ss_meta['sheets']}

    batch_requests = []
    if tab_name not in existing:
        batch_requests.append({'addSheet': {'properties': {'title': tab_name}}})
    # Remove the default blank 'Sheet1' tab once real tabs exist
    if 'Sheet1' in existing and tab_name != 'Sheet1':
        batch_requests.append({'deleteSheet': {'sheetId': existing['Sheet1']}})
    if batch_requests:
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={'requests': batch_requests}
        ).execute()

    values = list(csv.reader(io.StringIO(csv_content)))
    sheets_svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZZ"
    ).execute()
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A1",
        valueInputOption='RAW', body={'values': values}
    ).execute()

def ensure_attachment_on_drive(handle, filename, app_att_id):
    """Upload attachment to Drive under handle-named folder. Skip if handle folder already exists."""
    existing = drive_list(f"name='{_q(handle)}' and '{app_att_id}' in parents and mimeType='{FOLDER_MIME}'")
    if existing:
        return True
    try:
        resp = requests.get(f"{BASE_URL}/shared-files/{handle}/get_url/", headers=ss_headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"Failed to download attachment (Status {resp.status_code}): {handle}")
            return False
    except Exception as e:
        print(f"Error downloading attachment {handle}: {e}")
        return False
    handle_folder_id = get_or_create_folder(handle, app_att_id)
    upload_file(filename, resp.content, 'application/octet-stream', handle_folder_id)
    return True

def _collect_uploads(local_path, drive_parent_id, uploads):
    """Walk local tree, create Drive folders sequentially, collect files for parallel upload."""
    for item in sorted(local_path.iterdir()):
        if item.is_dir():
            sub_id = get_or_create_folder(item.name, drive_parent_id)
            _collect_uploads(item, sub_id, uploads)
        else:
            mime = 'text/csv' if item.suffix == '.csv' else 'application/json'
            uploads.append((item, mime, drive_parent_id))

def _upload_one(item, mime, parent_id):
    svc = _thread_drive_svc()
    svc.files().create(
        body={'name': item.name, 'parents': [parent_id]},
        media_body=MediaInMemoryUpload(item.read_bytes(), mimetype=mime),
        supportsAllDrives=True, fields='id'
    ).execute()

def upload_folder_to_drive(local_path, drive_parent_id):
    """Recursively upload a local temp folder to Drive (always create, no existence check)."""
    uploads = []
    _collect_uploads(local_path, drive_parent_id, uploads)
    print(f"  Uploading {len(uploads)} files ({10} workers)...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_upload_one, item, mime, pid) for item, mime, pid in uploads]
        for f in futures:
            f.result()

def rotate_old_backups():
    all_folders = drive_list(f"'{backup_root_id}' in parents and mimeType='{FOLDER_MIME}'")
    dated = sorted(
        [f for f in all_folders if f['name'] not in ('Latest', 'Attachments', 'Meta')],
        key=lambda f: f['name']
    )
    for old in dated[:-GENERATIONS]:
        print(f"Trashing old backup: {old['name']}")
        try:
            drive_svc.files().update(
                fileId=old['id'], body={'trashed': True}, supportsAllDrives=True
            ).execute()
        except Exception as e:
            print(f"  Warning (could not trash {old['name']}): {e}")

# ================= DRIVE FOLDER STRUCTURE =================
backup_root_id = GOOGLE_BACKUP_ROOT_FOLDER_ID

# Check if backup already ran today via local lock file (avoids needing Drive delete permissions)
if RUN_LOCK_FILE.exists() and RUN_LOCK_FILE.read_text().strip() == current_date:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Exiting: backup already exists for today.")
    sys.exit(0)

# Check network before creating any folders or writing the lock file
def _has_network(host="www.googleapis.com", port=443, timeout=10):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

if not _has_network():
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} No network — aborting, will retry next run.")
    sys.exit(1)

print(f"Backup timestamp: {timestamp}")

dated_folder_id     = get_or_create_folder(timestamp, backup_root_id)
attachments_root_id = get_or_create_folder('Attachments', backup_root_id)
meta_folder_id      = get_or_create_folder('Meta', backup_root_id)
latest_root_id      = get_or_create_folder('Latest', backup_root_id)

# Local temp staging — wiped at the end
temp_dir = Path(tempfile.mkdtemp(prefix='smartsuite_backup_'))

# ================= SMARTSUITE API =================
def get_solutions():
    r = requests.get(f"{BASE_URL}/solutions/?workspace={ACCOUNT_ID}", headers=ss_headers, timeout=TIMEOUT)
    if r.status_code != 200:
        print(f"Error loading solutions: {r.status_code}")
        return {}
    return {s['id']: s['name'] for s in r.json()}

def get_applications():
    r = requests.get(f"{BASE_URL}/applications/", headers=ss_headers, timeout=TIMEOUT)
    if r.status_code != 200:
        print(f"Can't load table list: {r.status_code}")
        return []
    return r.json()

def _ss_post(url, **kwargs):
    """POST to SmartSuite with retries on transient errors."""
    for attempt in range(3):
        try:
            r = requests.post(url, headers=ss_headers, timeout=TIMEOUT, **kwargs)
            return r
        except requests.exceptions.ConnectionError as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return None

def get_records(application_id):
    url = f"{BASE_URL}/applications/{application_id}/records/list/"
    all_records, offset, limit = [], 0, 1000
    while True:
        r = _ss_post(url, params={"offset": offset, "limit": limit},
                     json={"sort": [], "filter": {}})
        r.raise_for_status()
        records = r.json().get('items', [])
        all_records.extend(records)
        if len(records) < limit:
            break
        offset += limit
    return all_records

def generate_csv(app_id, fields):
    r = _ss_post(f"{BASE_URL}/applications/{app_id}/records/generate_csv/",
                 json={'visible_fields': fields})
    if r.status_code == 200:
        return r.content.decode('UTF-8')
    print(f"Failed to generate CSV for app {app_id}: {r.status_code}")
    return None

def file_attachment_fields(structure):
    return {f['slug']: f['label'] for f in structure if f['field_type'] == 'filefield'}

def inject_record_id(csv_content, records):
    """Prepend Record ID column to CSV, matched by Auto Number or by position."""
    rows = list(csv.reader(io.StringIO(csv_content)))
    if not rows:
        return csv_content
    header = rows[0]
    auto_col = next((i for i, h in enumerate(header) if h == 'Auto Number'), None)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Record ID'] + header)
    if auto_col is not None:
        auto_map = {int(r.get('autonumber', 0)): r['id'] for r in records if r.get('autonumber') is not None}
        for row in rows[1:]:
            try:
                rid = auto_map.get(int(row[auto_col].lstrip('#')), '')
            except (ValueError, IndexError):
                rid = ''
            w.writerow([rid] + row)
    else:
        for row, rec in zip(rows[1:], records):
            w.writerow([rec['id']] + row)
    return out.getvalue()


# ================= MAIN =================
try:
    solutions   = get_solutions()
    tables_data = get_applications()

    if not tables_data:
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} SmartSuite unreachable (VPN down?) — skipping, will retry next run.")
        sys.exit(1)

    _sheets_disabled = False
    tables_backed_up = 0
    solutions_seen = set()
    for table in tables_data:
        app_name         = table['name']
        app_id           = table['id']
        app_solution     = table['solution']
        table_structure  = table['structure']
        app_solution_name = solutions.get(app_solution, app_solution)

        if app_solution_name == app_solution or app_solution_name.startswith('תTest'):
            continue
        if MAX_SOLUTIONS is not None:
            solutions_seen.add(app_solution_name)
            if len(solutions_seen) > MAX_SOLUTIONS:
                break

        print(f'Backing up: {app_solution_name} -> {app_name}')
        try:
            clean_sol = app_solution_name.replace("/", "_")
            clean_app = app_name.replace("/", "_")

            sol_temp = temp_dir / clean_sol
            sol_temp.mkdir(exist_ok=True)

            # Drive folders for attachments
            sol_att_id = get_or_create_folder(clean_sol, attachments_root_id)
            app_att_id = get_or_create_folder(clean_app, sol_att_id)

            # 1. Fetch records + CSV
            fields      = [f['slug'] for f in table_structure if f['slug'] not in ["followed_by"]]
            records     = get_records(app_id)
            csv_content = generate_csv(app_id, fields)

            # 2. CSV with Record ID injected
            if csv_content:
                enhanced = inject_record_id(csv_content, records)
                (sol_temp / f"{clean_app}.csv").write_text(enhanced, encoding='utf-8-sig')

            # 3. Structure JSON
            (sol_temp / f"{clean_app}.structure.json").write_text(
                json.dumps(table_structure, indent=4, ensure_ascii=False), encoding='utf-8'
            )

            # 4. Attachments → Drive (deduped by handle via API, no local copy kept)
            file_fields = file_attachment_fields(table_structure)
            if file_fields:
                manifest = {}
                for record in records:
                    record_attachments = []
                    for slug, label in file_fields.items():
                        if slug in record and record[slug]:
                            for att in record[slug]:
                                handle   = att['handle']
                                filename = att['metadata']['filename']
                                uploaded = ensure_attachment_on_drive(handle, filename, app_att_id)
                                record_attachments.append({
                                    'field_slug':  slug,
                                    'field_label': label,
                                    'handle':      handle,
                                    'filename':    filename,
                                    'drive_path':  f"Attachments/{clean_sol}/{clean_app}/{handle}/{filename}",
                                    'uploaded':    uploaded
                                })
                    if record_attachments:
                        manifest[record['id']] = {
                            'title':       record.get('title', ''),
                            'attachments': record_attachments
                        }
                if manifest:
                    (sol_temp / f"{clean_app}.attachments.json").write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=4), encoding='utf-8'
                    )

            # 5. Latest → one spreadsheet per solution, one tab per table
            if csv_content and not _sheets_disabled:
                try:
                    sol_sheet_id = _get_or_create_solution_spreadsheet(clean_sol, latest_root_id)
                    upsert_tab(sol_sheet_id, clean_app, enhanced)
                except Exception as e:
                    if 'SERVICE_DISABLED' in str(e):
                        _sheets_disabled = True
                        print(f"  Warning: Sheets API disabled — skipping Latest updates for this run")
                    else:
                        print(f"  Warning (Latest/{clean_app}): {e}")

        except Exception as e:
            print(f"  ERROR backing up {app_solution_name} -> {app_name}: {e} — skipping")
        else:
            tables_backed_up += 1

    if tables_backed_up == 0:
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} No tables backed up — skipping lock file, will retry next run.")
        sys.exit(1)

    # Upload all staged files to Drive dated folder
    print(f"Uploading dated backup to Drive...")
    upload_folder_to_drive(temp_dir, dated_folder_id)

    # Upload meta
    upload_file('solutions.json', json.dumps(solutions,   ensure_ascii=False, indent=4).encode(), 'application/json', meta_folder_id)
    upload_file('tables.json',    json.dumps(tables_data, ensure_ascii=False, indent=4).encode(), 'application/json', meta_folder_id)

    rotate_old_backups()
    RUN_LOCK_FILE.write_text(current_date)
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Finished. ({tables_backed_up} tables backed up)")
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
