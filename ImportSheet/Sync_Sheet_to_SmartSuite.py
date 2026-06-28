# Prerequisites (same as SmartsuiteBackup):
# python3 -m pip install requests google-api-python-client google-auth google-auth-httplib2

import sys
sys.stdout.reconfigure(line_buffering=True)
import argparse, importlib.util, os, requests, json, time
from datetime import datetime, date, timedelta

try:
    from config import WEBHOOK_URL
except ImportError:
    WEBHOOK_URL = None

def load_jobs(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"Error: jobs file not found: {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("sync_jobs", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.JOBS

def load_config(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("SmartsuiteConfig", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ── SETTINGS ──────────────────────────────────────────────────────────────────
TIMEOUT        = 30
RETRY_ATTEMPTS = 3

# Google Sheets stores dates as days since this epoch
_SHEETS_EPOCH = date(1899, 12, 30)

# ── SMARTSUITE CLIENT ─────────────────────────────────────────────────────────
BASE_URL = "https://app.smartsuite.com/api/v1"

def _ss_post(url, **kwargs):
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return requests.post(url, headers=ss_headers, timeout=TIMEOUT, **kwargs)
        except requests.exceptions.ConnectionError:
            if attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(2 ** attempt)

def fetch_records(app_id, key_field):
    """Fetch all records; return ({key_value: record_id}, {key_value: full_record})."""
    url = f"{BASE_URL}/applications/{app_id}/records/list/"
    all_records, offset, limit = [], 0, 1000
    while True:
        r = _ss_post(url, params={"offset": offset, "limit": limit},
                     json={"sort": [], "filter": {}})
        r.raise_for_status()
        batch = r.json().get("items", [])
        all_records.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    print(f"  Fetched {len(all_records)} records from SmartSuite")
    key_map, rec_map = {}, {}
    for rec in all_records:
        raw = rec.get(key_field)
        if raw is not None:
            k = str(int(raw)) if isinstance(raw, (int, float)) else str(raw)
            key_map[k] = rec["id"]
            rec_map[k] = rec
    return key_map, rec_map

def send_slack(message):
    if not WEBHOOK_URL:
        return
    try:
        requests.post(WEBHOOK_URL, json={"text": message},
                      headers={"Content-Type": "application/json"}, timeout=10)
    except Exception as e:
        print(f"  Slack notification failed: {e}")

def patch_record(app_id, record_id, payload):
    url = f"{BASE_URL}/applications/{app_id}/records/{record_id}/"
    r = requests.patch(url, headers=ss_headers, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

_SELECT_TYPES = {"statusfield", "selectionfield", "singleselectfield"}

def fetch_app_meta(app_id):
    """Fetch app structure once; return (label_to_slug, select_maps).

    label_to_slug : {SmartSuite field label: slug}
    select_maps   : {slug: {option label: value code}} for single-select fields
    """
    r = requests.get(f"{BASE_URL}/applications/{app_id}/", headers=ss_headers, timeout=TIMEOUT)
    r.raise_for_status()
    label_to_slug, select_maps = {}, {}
    for f in r.json().get("structure", []):
        slug  = f.get("slug", "")
        label = f.get("label", "")
        ftype = f.get("field_type", "")
        if slug and label:
            label_to_slug[label] = slug
        if ftype in _SELECT_TYPES and slug:
            choices = f.get("params", {}).get("choices", [])
            select_maps[slug] = {
                ch["label"]: ch["value"]
                for ch in choices if "label" in ch and "value" in ch
            }
    return label_to_slug, select_maps

# ── GOOGLE SHEETS CLIENT ──────────────────────────────────────────────────────
import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build

def read_sheet(sheet_id, tab_name):
    """Return list of dicts (header → value) for every data row in the tab."""
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=tab_name,
        valueRenderOption="UNFORMATTED_VALUE",  # numbers stay numbers; dates are serials
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute()
    rows = result.get("values", [])
    if len(rows) < 2:
        return []
    headers = [str(h).strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))  # fill missing trailing cells
        out.append(dict(zip(headers, padded)))
    return out

# ── VALUE COERCION ────────────────────────────────────────────────────────────
def _is_date_serial(value):
    """Google Sheets date serials are floats in the plausible date range ~20000-60000 (1954–2064)."""
    return isinstance(value, (int, float)) and 20000 < float(value) < 60000

def _norm_date(s):
    """Normalise ISO date strings so '...Z' and '....000Z' compare equal."""
    return s.replace(".000Z", "Z") if isinstance(s, str) else s

def coerce(value, slug, col_name="", select_map=None):
    """Convert a Google Sheets cell value to a SmartSuite-compatible PATCH value."""
    hint = (slug + col_name).lower()
    if _is_date_serial(value) and ("date" in hint or "due" in hint):
        d = _SHEETS_EPOCH + timedelta(days=int(value))
        return {"date": d.strftime("%Y-%m-%dT00:00:00.000Z"), "include_time": False}
    if isinstance(value, datetime):
        return {"date": value.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "include_time": False}
    if isinstance(value, date):
        return {"date": value.strftime("%Y-%m-%dT00:00:00.000Z"), "include_time": False}
    if select_map and isinstance(value, str):
        return select_map.get(value, value)  # translate label → value code; fall back to raw
    return value

def current_comparable(ss_value):
    """Normalise a SmartSuite field value for equality comparison."""
    if isinstance(ss_value, dict):   # datefield: {'date': ..., 'include_time': ...}
        return _norm_date(ss_value.get("date") or "")
    return ss_value or ""

def new_comparable(coerced):
    """Normalise a coerced sheet value the same way for comparison."""
    if isinstance(coerced, dict):
        return _norm_date(coerced.get("date") or "")
    return coerced or ""

# ── JOB RUNNER ────────────────────────────────────────────────────────────────
def run_job(job, args):
    name       = job["name"]
    sheet_id   = job["sheet_id"]
    tab_name   = job["tab_name"]
    app_id     = job["app_id"]
    key_column = job["key_column"]
    key_field  = job["key_field"]
    dry_run = job.get("dry_run", False) or args.dry_run

    print(f"\n{'='*60}")
    print(f"Job: {name}{' [DRY RUN]' if dry_run else ''}")

    label_to_slug, auto_select_maps = fetch_app_meta(app_id)
    select_maps = {**auto_select_maps, **job.get("select_maps", {})}  # explicit config wins

    rows = read_sheet(sheet_id, tab_name)
    print(f"Rows: {len(rows)}")

    # Build slug-keyed field_map from config (values are SmartSuite field names).
    # If field_map is omitted, auto-match sheet column headers to SmartSuite field labels.
    raw_fm = job.get("field_map")
    if raw_fm:
        field_map = {}
        for sheet_col, ss_name in raw_fm.items():
            slug = label_to_slug.get(ss_name)
            if not slug:
                print(f"  Warning: SmartSuite field '{ss_name}' not found — skipping column '{sheet_col}'")
            else:
                field_map[sheet_col] = slug
    else:
        lower_to_slug = {k.lower(): v for k, v in label_to_slug.items()}
        sheet_headers = list(rows[0].keys()) if rows else []
        field_map = {h: lower_to_slug[h.lower()] for h in sheet_headers if h.lower() in lower_to_slug}
        print(f"  Auto-matched columns: {list(field_map.keys())}")
    key_map, rec_map = fetch_records(app_id, key_field)

    updated = skipped = unchanged = errors = 0

    for row in rows:
        key_raw   = row.get(key_column, "")
        key_value = str(int(key_raw)) if isinstance(key_raw, (int, float)) else str(key_raw).strip()
        if not key_value:
            skipped += 1
            continue

        record_id = key_map.get(key_value)
        if not record_id:
            print(f"  Not found: {key_column}={key_value}")
            skipped += 1
            continue

        ss_rec  = rec_map[key_value]
        payload = {}
        changes = []
        for col, ss_slug in field_map.items():
            raw = row.get(col, "")
            if raw in ("", None):
                continue
            coerced = coerce(raw, ss_slug, col, select_maps.get(ss_slug))
            cur = current_comparable(ss_rec.get(ss_slug))
            nw  = new_comparable(coerced)
            if cur != nw:
                payload[ss_slug] = coerced
                changes.append(f"    {col}: {cur!r} → {nw!r}")

        if not payload:
            unchanged += 1
            continue

        if dry_run:
            print(f"  #{key_value} would change:")
            for c in changes:
                print(c)
            updated += 1
            continue

        try:
            patch_record(app_id, record_id, payload)
            print(f"  Updated #{key_value}")
            updated += 1
        except Exception as e:
            print(f"  Error patching #{key_value}: {e}")
            errors += 1

    summary = f"{updated} {'would change' if dry_run else 'updated'}, {unchanged} already up-to-date, {skipped} skipped, {errors} errors"
    print(f"Result: {summary}")
    if not dry_run and (updated > 0 or errors > 0):
        icon = "🚨" if errors > 0 else "✅"
        parts = []
        if updated:
            parts.append(f"{updated} updated")
        if errors:
            parts.append(f"{errors} error{'s' if errors > 1 else ''}")
        send_slack(f"{icon} *SmartSuite Sync — {name}*\n{', '.join(parts)}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Google Sheets → SmartSuite")
    parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to SmartSuite")
    parser.add_argument("--jobs",   required=True, metavar="PATH", help="Path to sync_jobs.py")
    parser.add_argument("--config", required=True, metavar="PATH", help="Path to SmartsuiteConfig.py")
    args = parser.parse_args()

    cfg = load_config(args.config)
    ss_headers = {
        "accept":        "application/json",
        "Authorization": "Token " + cfg.TOKEN,
        "Content-Type":  "application/json",
        "ACCOUNT-ID":    cfg.ACCOUNT_ID,
    }
    _creds     = service_account.Credentials.from_service_account_file(
        cfg.GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    _http      = google_auth_httplib2.AuthorizedHttp(_creds, http=httplib2.Http(timeout=TIMEOUT))
    sheets_svc = build("sheets", "v4", http=_http)

    jobs = load_jobs(args.jobs)
    for job in jobs:
        run_job(job, args)
    print(f"\n{'='*60}")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} All jobs complete.")
