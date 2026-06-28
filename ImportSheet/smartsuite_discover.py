# Discovery helper — prints SmartSuite app IDs, field slugs, and select option codes.
# Run once to collect the values needed for sync_jobs.py.
#
# Usage:
#   python3 smartsuite_discover.py                    # list all apps grouped by solution
#   python3 smartsuite_discover.py <app_id>           # list fields for that app
#   python3 smartsuite_discover.py --search <keyword> # filter app list by name
#   python3 smartsuite_discover.py --raw              # dump raw /applications/ JSON

import sys, os, json, requests, argparse, importlib.util

def load_config(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("SmartsuiteConfig", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BASE_URL = "https://app.smartsuite.com/api/v1"
HEADERS  = {}  # populated in __main__ after config is loaded

SELECT_TYPES = {"statusfield", "selectionfield"}


def get(path):
    r = requests.get(BASE_URL + path, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def list_apps(search=None):
    apps = get("/applications/")
    if not apps:
        print("No applications found.")
        return

    # Try to get solution names; fall back to solution IDs if endpoint missing
    solution_names = {}
    try:
        for sol in get("/solutions/"):
            solution_names[sol["id"]] = sol.get("name") or sol["id"]
    except Exception:
        pass

    if search:
        apps = [a for a in apps if search.lower() in (a.get("name") or "").lower()]
        if not apps:
            print(f"No apps matching '{search}'.")
            return

    # Group by solution
    by_solution: dict[str, list] = {}
    for app in apps:
        sol_id   = app.get("solution", "")
        sol_name = solution_names.get(sol_id, sol_id or "No solution")
        by_solution.setdefault(sol_name, []).append(app)

    print(f"\nSmartSuite Apps  (run with <app_id> to see fields)\n")

    total = 0
    for sol_name in sorted(by_solution):
        print(f"Solution: {sol_name}")
        sol_apps = sorted(by_solution[sol_name], key=lambda a: a.get("name") or "")
        for i, app in enumerate(sol_apps):
            connector = "└─" if i == len(sol_apps) - 1 else "├─"
            indent    = "     " if i == len(sol_apps) - 1 else "│    "
            print(f"  {connector} {app.get('name', '(unnamed)')}")
            print(f"  {indent} app_id: {app['id']}")
            total += 1
        print()

    print(f"Total: {total} app{'s' if total != 1 else ''}")
    if not search:
        print("\nFilter by name:  python3 smartsuite_discover.py --search <keyword>")


def list_fields(app_id):
    try:
        app = get(f"/applications/{app_id}/")
    except requests.HTTPError as e:
        print(f"Error fetching app {app_id}: {e}")
        sys.exit(1)

    fields = app.get("structure", [])
    print(f"\nApp:    {app.get('name', app_id)}")
    print(f"app_id: {app_id}")
    print(f"Fields: {len(fields)}\n")

    select_fields = {}
    for i, f in enumerate(fields, 1):
        slug  = f.get("slug", "")
        label = f.get("label", "")
        ftype = f.get("field_type", "")
        print(f"  {i:>3}. {label}")
        print(f"       slug: {slug}")
        print(f"       type: {ftype}")
        if ftype in SELECT_TYPES:
            choices = f.get("params", {}).get("choices", [])
            select_fields[slug] = (label, choices)
            for ch in choices:
                print(f"         option: {ch.get('label', '')}  →  {ch.get('value', '')}")
        print()

    # Paste-ready blocks
    print("─" * 60)
    print('Copy into sync_jobs.py:\n')
    print('    "field_map": {')
    for f in fields:
        slug  = f.get("slug", "")
        label = f.get("label", "")
        if slug and label:
            print(f'        "{label}": "{slug}",')
    print("    },")

    if select_fields:
        print('\n    "select_maps": {')
        for slug, (label, choices) in select_fields.items():
            if not choices:
                continue
            print(f'        "{slug}": {{  # {label}')
            for ch in choices:
                print(f'            "{ch.get("label", "")}": "{ch.get("value", "")}",')
            print("        },")
        print("    },")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover SmartSuite app IDs and field slugs")
    parser.add_argument("--config", required=True, metavar="PATH", help="Path to SmartsuiteConfig.py")
    parser.add_argument("--raw",    action="store_true",            help="Dump raw /applications/ JSON")
    parser.add_argument("--search", metavar="KEYWORD",              help="Filter app list by name")
    parser.add_argument("app_id",   nargs="?",                      help="App ID to inspect fields")
    args = parser.parse_args()

    cfg = load_config(args.config)
    HEADERS.update({
        "accept":        "application/json",
        "Authorization": "Token " + cfg.TOKEN,
        "Content-Type":  "application/json",
        "ACCOUNT-ID":    cfg.ACCOUNT_ID,
    })

    if args.raw:
        print(json.dumps(get("/applications/"), ensure_ascii=False, indent=2))
    elif args.app_id:
        list_fields(args.app_id)
    elif args.search:
        list_apps(search=args.search)
    else:
        list_apps()
