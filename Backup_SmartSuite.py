# Installation prerequisites:
# python3 -m pip install requests google-api-python-client google-auth
# Version 5.0 - Full Drive API: dated backups + attachments on Drive, local temp staging only

import sys
sys.stdout.reconfigure(line_buffering=True)
import re
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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

TOKEN                        = cfg.TOKEN
ACCOUNT_ID                   = cfg.ACCOUNT_ID
GOOGLE_SERVICE_ACCOUNT_FILE  = cfg.GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHARED_DRIVE_ID       = cfg.GOOGLE_SHARED_DRIVE_ID
GOOGLE_BACKUP_ROOT_FOLDER_ID = cfg.GOOGLE_BACKUP_ROOT_FOLDER_ID
# Optional: list of solution-name prefixes to exclude from backup (e.g. ['Test', 'Archive'])
SKIP_SOLUTIONS               = getattr(cfg, 'SKIP_SOLUTIONS', [])

# ================= SETTINGS =================
GENERATIONS    = 7
TIMEOUT        = 60
MAX_SOLUTIONS  = None   # Set to an integer (e.g. 3) for debug runs; None = backup all
UPLOAD_WORKERS = 10     # parallel workers for dated-folder file uploads
ATT_WORKERS    = 5      # parallel workers for attachment uploads
FOLDER_MIME    = 'application/vnd.google-apps.folder'
SHEET_MIME     = 'application/vnd.google-apps.spreadsheet'
RUN_LOCK_FILE  = Path.home() / '.smartsuite_last_backup'

timestamp    = datetime.now().strftime("%Y-%m-%d %H:%M")
current_date = timestamp.split()[0]

BASE_URL = "https://app.smartsuite.com/api/v1"
ss_headers = {
    "accept":        "application/json",
    "Authorization": "Token " + TOKEN,
    "Content-Type":  "application/json",
    "ACCOUNT-ID":    ACCOUNT_ID,
}

# ================= GOOGLE DRIVE =================
_creds = service_account.Credentials.from_service_account_file(
    GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/spreadsheets']
)
_http = google_auth_httplib2.AuthorizedHttp(_creds, http=httplib2.Http(timeout=TIMEOUT))
drive_svc  = build('drive',  'v3', http=_http)
sheets_svc = build('sheets', 'v4', http=_http)

def _q(name):
    return name.replace("'", "\\'")

def _safe_name(name):
    """Sanitise a string for use as a Drive / filesystem name."""
    return re.sub(r'[/\\:*?"<>|]', '_', name)

_folder_cache      = {}
_folder_cache_lock = threading.Lock()
_solution_sheet_id = {}
_tab_cache         = {}   # spreadsheet_id -> {tab_name: sheet_id}
_thread_local      = threading.local()

def _thread_drive_svc():
    if not hasattr(_thread_local, 'svc'):
        http = google_auth_httplib2.AuthorizedHttp(_creds, http=httplib2.Http(timeout=TIMEOUT))
        _thread_local.svc = build('drive', 'v3', http=http)
    return _thread_local.svc

def _drive_list(svc, q, fields='files(id,name)'):
    """Paginated Drive list using the provided service object."""
    items, page_token = [], None
    while True:
        kwargs = dict(
            q=q + " and trashed=false",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            corpora='drive', driveId=GOOGLE_SHARED_DRIVE_ID,
            fields=f"nextPageToken,{fields}",
            pageSize=1000,
        )
        if page_token:
            kwargs['pageToken'] = page_token
        res = svc.files().list(**kwargs).execute()
        items.extend(res.get('files', []))
        page_token = res.get('nextPageToken')
        if not page_token:
            break
    return items

def drive_list(q, fields='files(id,name)'):
    return _drive_list(drive_svc, q, fields)

def get_or_create_folder(name, parent_id, svc=None):
    svc = svc or drive_svc
    key = (name, parent_id)
    with _folder_cache_lock:
        if key in _folder_cache:
            return _folder_cache[key]
    files = _drive_list(svc, f"name='{_q(name)}' and '{parent_id}' in parents and mimeType='{FOLDER_MIME}'")
    fid = files[0]['id'] if files else svc.files().create(
        body={'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]},
        supportsAllDrives=True, fields='id'
    ).execute()['id']
    with _folder_cache_lock:
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

def _get_or_create_solution_spreadsheet(sol_name, parent_id):
    """Return the spreadsheet ID for a solution's consolidated Latest sheet."""
    if sol_name in _solution_sheet_id:
        return _solution_sheet_id[sol_name]
    files = drive_list(f"name='{_q(sol_name)}' and '{parent_id}' in parents and mimeType='{SHEET_MIME}'")
    sid = files[0]['id'] if files else drive_svc.files().create(
        body={'name': sol_name, 'mimeType': SHEET_MIME, 'parents': [parent_id]},
        supportsAllDrives=True, fields='id'
    ).execute()['id']
    _solution_sheet_id[sol_name] = sid
    return sid

def upsert_tab(spreadsheet_id, tab_name, csv_content):
    """Create or overwrite a tab in an existing spreadsheet (caches metadata per run)."""
    if spreadsheet_id not in _tab_cache:
        ss_meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        _tab_cache[spreadsheet_id] = {
            s['properties']['title']: s['properties']['sheetId']
            for s in ss_meta['sheets']
        }
    existing = _tab_cache[spreadsheet_id]

    batch_requests = []
    if tab_name not in existing:
        batch_requests.append({'addSheet': {'properties': {'title': tab_name}}})
    if 'Sheet1' in existing and tab_name != 'Sheet1':
        batch_requests.append({'deleteSheet': {'sheetId': existing['Sheet1']}})
    if batch_requests:
        result = sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={'requests': batch_requests}
        ).execute()
        for reply in result.get('replies', []):
            if 'addSheet' in reply:
                props = reply['addSheet']['properties']
                existing[props['title']] = props['sheetId']
        if 'Sheet1' in existing and tab_name != 'Sheet1':
            del existing['Sheet1']

    sheets_svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZZ"
    ).execute()
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A1",
        valueInputOption='RAW',
        body={'values': list(csv.reader(io.StringIO(csv_content)))}
    ).execute()

def ensure_attachment_on_drive(handle, filename, app_att_id):
    """Upload attachment to Drive under handle-named folder. Skip if already uploaded."""
    svc = _thread_drive_svc()
    if _drive_list(svc, f"name='{_q(handle)}' and '{app_att_id}' in parents and mimeType='{FOLDER_MIME}'"):
        return True
    try:
        resp = requests.get(f"{BASE_URL}/shared-files/{handle}/get_url/", headers=ss_headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  Failed to download attachment (Status {resp.status_code}): {handle}")
            return False
    except Exception as e:
        print(f"  Error downloading attachment {handle}: {e}")
        return False
    handle_folder_id = get_or_create_folder(handle, app_att_id, svc=svc)
    svc.files().create(
        body={'name': filename, 'parents': [handle_folder_id]},
        media_body=MediaInMemoryUpload(resp.content, mimetype='application/octet-stream'),
        supportsAllDrives=True, fields='id'
    ).execute()
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
    print(f"  Uploading {len(uploads)} files ({UPLOAD_WORKERS} workers)...")
    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as executor:
        for f in [executor.submit(_upload_one, item, mime, pid) for item, mime, pid in uploads]:
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

if RUN_LOCK_FILE.exists() and RUN_LOCK_FILE.read_text().strip() == current_date:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Exiting: backup already exists for today.")
    sys.exit(0)

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
    """POST to SmartSuite with retries on rate limits, server errors, and connection failures."""
    delay = 2
    for attempt in range(6):
        try:
            r = requests.post(url, headers=ss_headers, timeout=TIMEOUT, **kwargs)
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', delay))
                print(f"  Rate limited — retrying in {wait}s (attempt {attempt + 1}/6)")
                time.sleep(wait)
                delay = min(delay * 2, 60)
            elif r.status_code >= 500 and attempt < 5:
                print(f"  Server error {r.status_code} — retrying in {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                return r
        except requests.exceptions.ConnectionError:
            if attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    return r  # return last response after exhausting retries

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
    print(f"  Failed to generate CSV for app {app_id}: {r.status_code}")
    return None

def file_attachment_fields(structure):
    return {f['slug']: f['label'] for f in structure if f['field_type'] == 'filefield'}

def inject_record_id(csv_content, records):
    """Prepend Record ID column to CSV, matched by Auto Number or by position."""
    rows = list(csv.reader(io.StringIO(csv_content)))
    if not rows:
        return csv_content
    header   = rows[0]
    auto_col = next((i for i, h in enumerate(header) if h == 'Auto Number'), None)
    out = io.StringIO()
    w   = csv.writer(out)
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
        if len(rows) - 1 != len(records):
            print(f"  Warning: CSV has {len(rows) - 1} data rows but fetched {len(records)} records — Record IDs may be misaligned")
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

    def _should_skip(sol_id, sol_name):
        if sol_name == sol_id:  # ID not resolved → unknown solution
            return True
        return any(sol_name.startswith(p) for p in SKIP_SOLUTIONS)

    tables_to_backup = [
        t for t in tables_data
        if not _should_skip(t['solution'], solutions.get(t['solution'], t['solution']))
    ]

    if MAX_SOLUTIONS is not None:
        seen, filtered = set(), []
        for t in tables_to_backup:
            sol_name = solutions.get(t['solution'], t['solution'])
            if sol_name not in seen and len(seen) >= MAX_SOLUTIONS:
                break
            seen.add(sol_name)
            filtered.append(t)
        tables_to_backup = filtered

    total            = len(tables_to_backup)
    _sheets_disabled = False
    tables_backed_up = 0

    for idx, table in enumerate(tables_to_backup, 1):
        app_name          = table['name']
        app_id            = table['id']
        app_solution      = table['solution']
        table_structure   = table['structure']
        app_solution_name = solutions.get(app_solution, app_solution)

        print(f'[{idx}/{total}] Backing up: {app_solution_name} -> {app_name}')
        try:
            clean_sol = _safe_name(app_solution_name)
            clean_app = _safe_name(app_name)

            sol_temp = temp_dir / clean_sol
            sol_temp.mkdir(exist_ok=True)

            sol_att_id = get_or_create_folder(clean_sol, attachments_root_id)
            app_att_id = get_or_create_folder(clean_app, sol_att_id)

            # 1. Fetch records + CSV
            fields      = [f['slug'] for f in table_structure if f['slug'] not in ["followed_by"]]
            records     = get_records(app_id)
            csv_content = generate_csv(app_id, fields)

            # 2. CSV with Record ID injected
            enhanced = None
            if csv_content:
                enhanced = inject_record_id(csv_content, records)
                (sol_temp / f"{clean_app}.csv").write_text(enhanced, encoding='utf-8-sig')

            # 3. Structure JSON
            (sol_temp / f"{clean_app}.structure.json").write_text(
                json.dumps(table_structure, indent=4, ensure_ascii=False), encoding='utf-8'
            )

            # 4. Attachments → Drive in parallel (deduped by handle)
            file_fields = file_attachment_fields(table_structure)
            if file_fields:
                att_tasks = []
                for record in records:
                    for slug, label in file_fields.items():
                        for att in (record.get(slug) or []):
                            att_tasks.append((
                                att['handle'], att['metadata']['filename'],
                                slug, label, record['id'], record.get('title', '')
                            ))
                manifest = {}
                if att_tasks:
                    print(f"  Uploading {len(att_tasks)} attachments ({ATT_WORKERS} workers)...")
                    with ThreadPoolExecutor(max_workers=ATT_WORKERS) as ex:
                        future_map = {
                            ex.submit(ensure_attachment_on_drive, h, fn, app_att_id): (h, fn, slug, label, rid, title)
                            for h, fn, slug, label, rid, title in att_tasks
                        }
                        for future in as_completed(future_map):
                            h, fn, slug, label, rid, title = future_map[future]
                            uploaded = future.result()
                            if rid not in manifest:
                                manifest[rid] = {'title': title, 'attachments': []}
                            manifest[rid]['attachments'].append({
                                'field_slug':  slug,
                                'field_label': label,
                                'handle':      h,
                                'filename':    fn,
                                'drive_path':  f"Attachments/{clean_sol}/{clean_app}/{h}/{fn}",
                                'uploaded':    uploaded,
                            })
                if manifest:
                    (sol_temp / f"{clean_app}.attachments.json").write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=4), encoding='utf-8'
                    )

            # 5. Latest → one spreadsheet per solution, one tab per table
            if enhanced and not _sheets_disabled:
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

    print(f"Uploading dated backup to Drive...")
    upload_folder_to_drive(temp_dir, dated_folder_id)

    upload_file('solutions.json', json.dumps(solutions,   ensure_ascii=False, indent=4).encode(), 'application/json', meta_folder_id)
    upload_file('tables.json',    json.dumps(tables_data, ensure_ascii=False, indent=4).encode(), 'application/json', meta_folder_id)

    rotate_old_backups()
    RUN_LOCK_FILE.write_text(current_date)
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Finished. ({tables_backed_up} tables backed up)")
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
