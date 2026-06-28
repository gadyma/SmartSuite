# python3 -m pip install requests google-api-python-client google-auth
# Version 2.0 - Permissions Export → Google Sheet

import requests
import os
import sys
import argparse
import importlib.util
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.oauth2 import service_account
from googleapiclient.discovery import build

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

parser = argparse.ArgumentParser(description="SmartSuite Permissions Audit")
parser.add_argument("--config", required=True, metavar="PATH", help="Path to SmartsuiteConfig.py")
args = parser.parse_args()
cfg = load_config(args.config)

TOKEN                       = cfg.TOKEN
ACCOUNT_ID                  = cfg.ACCOUNT_ID
GOOGLE_SERVICE_ACCOUNT_FILE = cfg.GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHEET_PERMISSIONS_ID = cfg.GOOGLE_SHEET_PERMISSIONS_ID

_creds = service_account.Credentials.from_service_account_file(
    GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
sheets_svc = build('sheets', 'v4', credentials=_creds)

base_url = "https://app.smartsuite.com/api/"
headers  = {"accept": "application/json", "Authorization": "Token " + TOKEN, "ACCOUNT-ID": ACCOUNT_ID}

USER_TYPE_LABEL = {'1': 'Member', '2': 'Admin', '3': 'Guest'}


def api_get(url, max_retries=6):
    delay = 2
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers)
        if resp.status_code != 429:
            return resp
        wait = int(resp.headers.get('Retry-After', delay))
        print(f"Rate limited on {url} — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(wait)
        delay = min(delay * 2, 60)
    return resp


def get_label_from_slug(data, field_slug):
    for field in data:
        if field['slug'] == field_slug:
            return field['label']
    return None


def get_solutions():
    resp = requests.get(base_url + "v1/solutions/", headers=headers)
    if resp.status_code != 200:
        print(f"Error loading solutions: {resp.status_code}")
        sys.exit(1)
    print('Solutions List Loaded Successfully')
    solutions = {}
    for s in resp.json():
        solutions[s['id']] = {
            'id':          s['id'],
            'name':        s['name'],
            'status':      s['status'],
            'permissions': s['permissions'],
        }
    return solutions


def get_tables():
    resp = requests.get(base_url + "v1/applications/", headers=headers)
    if resp.status_code != 200:
        print(f"Can't load Table list, error: {resp.status_code}")
        sys.exit(1)
    print('Tables List Loaded Successfully')
    tables = {}
    team   = None
    for table in resp.json():
        if table['solution'] not in solutions:
            continue
        sol_name = solutions[table['solution']]['name']
        tables[table['id']] = {
            'id':          table['id'],
            'name':        table['name'],
            'status':      table['status'],
            'solution':    sol_name,
            'permissions': table['permissions'],
        }
        if team is None and sol_name == 'System' and table['name'] == 'Teams':
            team = table['id']
            print('Table Teams found')
    return tables, team


def get_users():
    resp = requests.post(base_url + "v1/applications/members/records/list/", headers=headers)
    if resp.status_code != 200:
        print(f"Error loading users: {resp.status_code}")
        sys.exit(1)
    print('Users List Loaded Successfully')
    users = {}
    for s in resp.json()['items']:
        if not s['deleted_date']['date'] and s['type'] != '6':
            users[s['id']] = {
                'id':         s['id'],
                'full_name':  s['full_name']['sys_root'],
                'email':      s['email'][0],
                'type':       s['type'],
                'role':       s['role'],
                'last_login': s['last_login']['date'],
                'locale':     s['locale'],
            }
    return users


def get_teams():
    resp = requests.post(
        base_url + 'v1/applications/' + teams_table + '/records/list/',
        params={"offset": 0},
        headers=headers,
    )
    if resp.status_code != 200:
        print(f"Error loading teams: {resp.status_code}\n{resp.text}")
        sys.exit(1)
    print('Teams List Loaded Successfully')
    teams = {}
    for team in resp.json()['items']:
        teams[team['id']] = {
            'id':      team['id'],
            'name':    team['name'],
            'status':  team['status'],
            'members': team['members'],
        }
    return teams


def get_fields_permission():
    def fetch_table(key, item):
        resp = api_get(base_url + "v1/applications/" + key)
        if resp.status_code != 200:
            print(f"Can't load fields for table {key}: {resp.status_code}")
            return []
        print(f'Table: {key} fields Loaded Successfully')
        data = resp.json()
        results = []
        for p in data.get('field_permissions', []):
            label = get_label_from_slug(data['structure'], p['field_slug'])
            if label is None:
                continue
            results.append({
                'field_slug': p['field_slug'],
                'solution':   item['solution'],
                'table':      item['name'],
                'field':      label,
                'read':       p['read'],
                'write':      p['write'],
                'owners':     p.get('owners', ''),
                'private_to': p.get('private_to', ''),
            })
        return results

    field_permissions = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        for k, v in TN.items():
            futures[executor.submit(fetch_table, k, v)] = k
            time.sleep(0.4)
        for future in as_completed(futures):
            field_permissions.extend(future.result())
    return field_permissions


def recursive_replace_uid(obj):
    if isinstance(obj, str):
        if obj in user_names:
            u     = user_names[obj]
            name  = u['full_name'] or u['email']
            label = USER_TYPE_LABEL.get(u['type'], '')
            return f"{name} ({label})" if label else name
        if obj in teams_names:
            return teams_names[obj]['name']
        return obj
    elif isinstance(obj, dict):
        return {k: recursive_replace_uid(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [recursive_replace_uid(item) for item in obj]
    else:
        return obj


# ── main ──────────────────────────────────────────────────────────────────────
solutions             = get_solutions()
(tables_names, teams_table) = get_tables()
user_names            = get_users()
teams_names           = get_teams()
sol                   = recursive_replace_uid(solutions)
TN                    = recursive_replace_uid(tables_names)

field_permissions = get_fields_permission()
field_perm        = recursive_replace_uid(field_permissions)

HEADERS = [
    "Type", "Solution", "Table", "Field", "Field Slug",
    "audience",       "members",       "teams",       "roles",
    "audience_read",  "members_read",  "teams_read",  "roles_read",
    "audience_write", "members_write", "teams_write", "roles_write",
    "owners", "private_to",
]

timestamp_row = [f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
all_rows = [timestamp_row, HEADERS]

for (k, i) in sol.items():
    p = i['permissions']
    all_rows.append([
        "solution", i['name'], "", "", "",
        p.get('level', ''), p.get('members', ''), p.get('teams', ''), p.get('roles', ''),
        "", "", "", "",
        "", "", "", "",
        p.get('owners', ''), p.get('private_to', ''),
    ])

for (k, i) in TN.items():
    p = i['permissions']
    all_rows.append([
        "table", i['solution'], i['name'], "", "",
        p.get('level', ''), p.get('members', ''), p.get('teams', ''), p.get('roles', ''),
        "", "", "", "",
        "", "", "", "",
        p.get('owners', ''), p.get('private_to', ''),
    ])

for fld in field_perm:
    r = fld['read']
    w = fld['write']
    all_rows.append([
        "field", fld['solution'], fld['table'], fld['field'], fld['field_slug'],
        "", "", "", "",
        r.get('audience', ''), r.get('members', ''), r.get('teams', ''), r.get('roles', ''),
        w.get('audience', ''), w.get('members', ''), w.get('teams', ''), w.get('roles', ''),
        fld.get('owners', ''), fld.get('private_to', ''),
    ])

# ── Update Google Sheet ────────────────────────────────────────────────────────
print(f"Updating Google Sheet {GOOGLE_SHEET_PERMISSIONS_ID} ...")

spreadsheet = sheets_svc.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_PERMISSIONS_ID).execute()
sheet_id    = spreadsheet['sheets'][0]['properties']['sheetId']

sheets_svc.spreadsheets().values().clear(
    spreadsheetId=GOOGLE_SHEET_PERMISSIONS_ID, range='A:ZZZ'
).execute()
sheets_svc.spreadsheets().values().update(
    spreadsheetId=GOOGLE_SHEET_PERMISSIONS_ID,
    range='A1',
    valueInputOption='RAW',
    body={'values': [[str(c) if c is not None else '' for c in row] for row in all_rows]}
).execute()

sheets_svc.spreadsheets().batchUpdate(
    spreadsheetId=GOOGLE_SHEET_PERMISSIONS_ID,
    body={'requests': [
        {
            'updateSheetProperties': {
                'properties': {
                    'sheetId':        sheet_id,
                    'gridProperties': {'frozenRowCount': 2},
                },
                'fields': 'gridProperties.frozenRowCount',
            }
        },
        {
            'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': 2},
                'cell': {
                    'userEnteredFormat': {
                        'textFormat':      {'bold': True},
                        'backgroundColor': {'red': 0.85, 'green': 0.85, 'blue': 0.85},
                    }
                },
                'fields': 'userEnteredFormat(textFormat,backgroundColor)',
            }
        },
    ]}
).execute()

print(f"Google Sheet updated: {len(all_rows) - 2} data rows written.")
