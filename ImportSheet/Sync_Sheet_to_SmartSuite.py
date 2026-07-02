# Prerequisites (same as SmartsuiteBackup):
# python3 -m pip install requests google-api-python-client google-auth google-auth-httplib2

import sys
sys.stdout.reconfigure(line_buffering=True)
import argparse, importlib.util, os, requests, json, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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
TIMEOUT             = 30
RETRY_ATTEMPTS      = 5
CONCURRENT_WORKERS  = 5

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

def send_slack(message, webhook_url):
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"text": message},
                      headers={"Content-Type": "application/json"}, timeout=10)
    except Exception as e:
        print(f"  Slack notification failed: {e}")

def _ss_request(method, url, **kwargs):
    for attempt in range(RETRY_ATTEMPTS):
        r = method(url, headers=ss_headers, timeout=TIMEOUT, **kwargs)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()  # raise after exhausting retries

def patch_record(app_id, record_id, payload):
    _ss_request(requests.patch,
                f"{BASE_URL}/applications/{app_id}/records/{record_id}/",
                json=payload)

def create_record(app_id, payload):
    r = _ss_request(requests.post,
                    f"{BASE_URL}/applications/{app_id}/records/",
                    json=payload)
    return r.json()

_SELECT_TYPES  = {"statusfield", "selectionfield", "singleselectfield"}
_DATE_TYPES    = {"datefield", "duedatefield"}
_RICHTEXT_TYPES = {"richtextareafield"}

def fetch_app_meta(app_id):
    """Fetch app structure once; return (label_to_slug, select_maps, field_types).

    label_to_slug : {SmartSuite field label: slug}
    select_maps   : {slug: {option label: value code}} for single-select fields
    field_types   : {slug: field_type_string} for every field
    """
    r = requests.get(f"{BASE_URL}/applications/{app_id}/", headers=ss_headers, timeout=TIMEOUT)
    r.raise_for_status()
    label_to_slug, select_maps, field_types = {}, {}, {}
    for f in r.json().get("structure", []):
        slug  = f.get("slug", "")
        label = f.get("label", "")
        ftype = f.get("field_type", "")
        if slug and label:
            label_to_slug[label] = slug
        if slug and ftype:
            field_types[slug] = ftype
        if ftype in _SELECT_TYPES and slug:
            choices = f.get("params", {}).get("choices", [])
            select_maps[slug] = {
                ch["label"]: ch["value"]
                for ch in choices if "label" in ch and "value" in ch
            }
    return label_to_slug, select_maps, field_types

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

def _prosemirror_to_text(node):
    """Recursively extract plain text from a ProseMirror doc node."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_prosemirror_to_text(c) for c in node.get("content", [])]
    if node.get("type") in ("paragraph", "heading", "blockquote", "listItem", "bulletList", "orderedList"):
        return "".join(parts) + "\n"
    return "".join(parts)

def _str_to_richtext(text):
    """Wrap a plain string in ProseMirror doc format for SmartSuite richtextareafield."""
    return {
        "data": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}] if text else []
                }
            ]
        }
    }

def coerce(value, slug, col_name="", select_map=None, is_date=False, is_richtext=False):
    """Convert a Google Sheets cell value to a SmartSuite-compatible PATCH value."""
    hint = (slug + col_name).lower()
    if _is_date_serial(value) and (is_date or "date" in hint or "due" in hint):
        d = _SHEETS_EPOCH + timedelta(days=int(value))
        return {"date": d.strftime("%Y-%m-%dT00:00:00.000Z"), "include_time": False}
    if isinstance(value, datetime):
        return {"date": value.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "include_time": False}
    if isinstance(value, date):
        return {"date": value.strftime("%Y-%m-%dT00:00:00.000Z"), "include_time": False}
    if select_map and isinstance(value, str):
        if value in select_map:
            return select_map[value]
        # Case-insensitive fallback (e.g. sheet has "In progress", SmartSuite has "In Progress")
        lower_map = {k.lower(): v for k, v in select_map.items()}
        return lower_map.get(value.lower(), value)
    if is_richtext and isinstance(value, str):
        return _str_to_richtext(value)
    return value

def current_comparable(ss_value):
    """Normalise a SmartSuite field value for equality comparison."""
    if isinstance(ss_value, dict):
        if "data" in ss_value:   # richtextareafield
            return _prosemirror_to_text(ss_value["data"]).rstrip("\n")
        if "date" in ss_value:   # datefield / duedatefield
            return _norm_date(ss_value["date"] or "")
        if "value" in ss_value:  # statusfield / selectionfield / singleselectfield
            return ss_value["value"] or ""
        return ""
    return ss_value or ""

def new_comparable(coerced):
    """Normalise a coerced sheet value the same way for comparison."""
    if isinstance(coerced, dict):
        if "data" in coerced:    # richtextareafield
            return _prosemirror_to_text(coerced["data"]).rstrip("\n")
        return _norm_date(coerced.get("date") or "")
    return coerced or ""

# ── JOB RUNNER ────────────────────────────────────────────────────────────────
def run_job(job, args):
    name         = job["name"]
    sheet_id     = job["sheet_id"]
    tab_name     = job["tab_name"]
    app_id       = job["app_id"]
    key_column   = job["key_column"]
    key_field    = job["key_field"]
    upsert          = job.get("upsert", False)
    dry_run         = job.get("dry_run", False) or args.dry_run
    rename_tpl      = job.get("rename_app")
    exclude_columns = set(job.get("exclude_columns", []))
    # Per-job Slack webhook; falls back to global WEBHOOK_URL if not specified.
    # Set to None or omit to use global; set to False to disable Slack for this job.
    slack_webhook = job.get("slack_webhook", WEBHOOK_URL)

    print(f"\n{'='*60}")
    print(f"Job: {name}{' [DRY RUN]' if dry_run else ''}")

    # Optional app rename — {now} → current date/time
    if rename_tpl:
        new_name = rename_tpl.replace("{now}", datetime.now().strftime("%Y-%m-%d %H:%M"))
        if not dry_run:
            r = requests.patch(f"{BASE_URL}/applications/{app_id}/", headers=ss_headers,
                               json={"name": new_name}, timeout=TIMEOUT)
            r.raise_for_status()
        print(f"  {'Would rename' if dry_run else 'Renamed'} app → {new_name!r}")

    label_to_slug, auto_select_maps, field_types = fetch_app_meta(app_id)
    select_maps = {**auto_select_maps, **job.get("select_maps", {})}

    rows = read_sheet(sheet_id, tab_name)
    print(f"Rows: {len(rows)}")

    # Build slug-keyed field_map. If omitted, auto-match sheet headers to SmartSuite labels.
    raw_fm = job.get("field_map")
    if raw_fm:
        field_map = {}
        for sheet_col, ss_name in raw_fm.items():
            if sheet_col in exclude_columns:
                continue
            slug = label_to_slug.get(ss_name)
            if not slug:
                print(f"  Warning: SmartSuite field '{ss_name}' not found — skipping column '{sheet_col}'")
            else:
                field_map[sheet_col] = slug
    else:
        lower_to_slug = {k.lower(): v for k, v in label_to_slug.items()}
        sheet_headers = list(rows[0].keys()) if rows else []
        field_map = {h: lower_to_slug[h.lower()] for h in sheet_headers
                     if h.lower() in lower_to_slug and h not in exclude_columns}
        print(f"  Auto-matched columns: {list(field_map.keys())}")

    key_map, rec_map = fetch_records(app_id, key_field)

    # ── Classify every row into create / update / skip ─────────────────────────
    to_create  = []   # [(key_value, payload)]
    to_update  = []   # [(key_value, record_id, payload, changes)]
    skipped = unchanged = 0

    for row in rows:
        key_raw   = row.get(key_column, "")
        key_value = str(int(key_raw)) if isinstance(key_raw, (int, float)) else str(key_raw).strip()
        if not key_value:
            skipped += 1
            continue

        record_id = key_map.get(key_value)
        if not record_id:
            if not upsert:
                skipped += 1
                continue
            create_payload = {}
            for col, ss_slug in field_map.items():
                raw = row.get(col, "")
                if raw not in ("", None):
                    ftype = field_types.get(ss_slug, "")
                    create_payload[ss_slug] = coerce(raw, ss_slug, col, select_maps.get(ss_slug),
                                                     is_date=(ftype in _DATE_TYPES),
                                                     is_richtext=(ftype in _RICHTEXT_TYPES))
            if key_field not in create_payload:
                create_payload[key_field] = key_value
            to_create.append((key_value, create_payload))
            continue

        ss_rec  = rec_map[key_value]
        payload = {}
        changes = []
        for col, ss_slug in field_map.items():
            raw = row.get(col, "")
            if raw in ("", None):
                continue
            ftype   = field_types.get(ss_slug, "")
            coerced = coerce(raw, ss_slug, col, select_maps.get(ss_slug),
                             is_date=(ftype in _DATE_TYPES),
                             is_richtext=(ftype in _RICHTEXT_TYPES))
            cur = current_comparable(ss_rec.get(ss_slug))
            nw  = new_comparable(coerced)
            if cur != nw:
                payload[ss_slug] = coerced
                changes.append(f"    {col}: {cur!r} → {nw!r}")

        if not payload:
            unchanged += 1
        else:
            to_update.append((key_value, record_id, payload, changes))

    # Show which fields are driving changes (helps catch false-positive comparisons)
    if to_update:
        field_counts = Counter(
            c.strip().split(":")[0]
            for _, _, _, chgs in to_update
            for c in chgs
        )
        top = ", ".join(f"{col} ({n})" for col, n in field_counts.most_common(8))
        print(f"  Fields with changes: {top}")

    # ── Dry-run: just print what would happen ──────────────────────────────────
    if dry_run:
        for key_value, payload in to_create:
            print(f"  Would create: {key_column}={key_value!r}")
            for k, v in payload.items():
                print(f"    {k}: {v!r}")
        for key_value, _, _, changes in to_update:
            print(f"  #{key_value} would change:")
            for c in changes:
                print(c)
        created, updated, errors = len(to_create), len(to_update), 0
    else:
        # ── Execute creates concurrently ───────────────────────────────────────
        created = errors = 0
        if to_create:
            with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as ex:
                futures = {ex.submit(create_record, app_id, p): kv for kv, p in to_create}
                for fut in as_completed(futures):
                    kv = futures[fut]
                    try:
                        fut.result()
                        created += 1
                    except Exception as e:
                        print(f"  Error creating {kv!r}: {e}")
                        errors += 1
            print(f"  Created {created} records")

        # ── Execute updates concurrently ───────────────────────────────────────
        updated = 0
        if to_update:
            with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as ex:
                futures = {ex.submit(patch_record, app_id, rid, p): kv
                           for kv, rid, p, _ in to_update}
                for fut in as_completed(futures):
                    kv = futures[fut]
                    try:
                        fut.result()
                        updated += 1
                    except Exception as e:
                        print(f"  Error patching {kv!r}: {e}")
                        errors += 1
            print(f"  Updated {updated} records")

    parts_summary = []
    if updated:
        parts_summary.append(f"{updated} {'would update' if dry_run else 'updated'}")
    if created:
        parts_summary.append(f"{created} {'would create' if dry_run else 'created'}")
    parts_summary += [f"{unchanged} already up-to-date", f"{skipped} skipped", f"{errors} errors"]
    print(f"Result [{name}]: {', '.join(parts_summary)}")
    if not dry_run and (updated > 0 or created > 0 or errors > 0):
        icon = "🚨" if errors > 0 else "✅"
        parts = []
        if updated:
            parts.append(f"{updated} updated")
        if created:
            parts.append(f"{created} created")
        if errors:
            parts.append(f"{errors} error{'s' if errors > 1 else ''}")
        send_slack(f"{icon} *SmartSuite Sync*\n*Job:* {name}\n{', '.join(parts)}", slack_webhook)

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
