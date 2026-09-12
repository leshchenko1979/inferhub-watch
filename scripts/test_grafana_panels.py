#!/usr/bin/env python3
"""
grafana-test-panels.py — Automated validator for Grafana dashboard panel queries.

Loads dashboard JSON, extracts queries for each panel, executes them against
live Grafana `/api/ds/query` endpoint with specified time range, and validates
that every panel returns valid, non-empty data without SQL/HTTP errors.

Usage:
    ./scripts/grafana-test-panels.py [options] [dashboard_json_path]

Options:
    --from <time>       Grafana time from (default: now-24h)
    --to <time>         Grafana time to (default: now)
    --uid <uid>         Test dashboard by UID from Grafana API instead of file
    --all               Test all provisioned dashboards in repo
    --verbose           Show detailed query execution output
    --help              Show this message
"""

import os
import sys
import json
import glob
import argparse
import urllib.request
import urllib.error


def resolve_grafana_api_key(repo_dir):
    key = os.environ.get("GRAFANA_API_KEY", "")
    if not key:
        env_file = os.path.join(repo_dir, ".env")
        if os.path.isfile(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GRAFANA_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
                        break
    return key


def get_dashboard_panels(dash_doc):
    """Recursively extract all panels including row sub-panels."""
    panels = []
    for p in dash_doc.get("panels", []):
        ptype = p.get("type")
        if ptype == "row":
            panels.append(p)
            for sub in p.get("panels", []):
                panels.append(sub)
        else:
            panels.append(p)
    return panels


def execute_panel_query(base_url, api_key, panel, time_from="now-24h", time_to="now"):
    pid = panel.get("id")
    title = panel.get("title", f"Panel {pid}")
    ptype = panel.get("type", "unknown")
    targets = panel.get("targets", [])

    if ptype == "row" or not targets:
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "SKIP",
            "message": "Row or no queries",
            "rows": 0,
            "sample": ""
        }

    queries = []
    for idx, t in enumerate(targets):
        raw_sql = t.get("rawSql")
        if not raw_sql:
            continue

        ref_id = t.get("refId") or chr(ord("A") + idx)
        ds = t.get("datasource") or panel.get("datasource")
        if isinstance(ds, str):
            ds = {"uid": ds}
        elif not isinstance(ds, dict):
            ds = {"uid": "inferhub-pg", "type": "grafana-postgresql-datasource"}

        # Macro substitutions for Grafana SQL datasource if needed
        # Grafana /api/ds/query handles $__timeFilter and $__timeGroup automatically for PostgreSQL plugin
        queries.append({
            "refId": ref_id,
            "datasource": ds,
            "rawSql": raw_sql,
            "format": t.get("format", "table")
        })

    if not queries:
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "SKIP",
            "message": "No SQL queries in targets",
            "rows": 0,
            "sample": ""
        }

    payload = {
        "queries": queries,
        "from": time_from,
        "to": time_to
    }

    req = urllib.request.Request(
        f"{base_url}/api/ds/query",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}: {e.reason}"
        try:
            body = json.loads(e.read().decode("utf-8"))
            if "message" in body:
                err_msg += f" - {body['message']}"
        except Exception:
            pass
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "ERROR",
            "message": err_msg,
            "rows": 0,
            "sample": ""
        }
    except Exception as e:
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "ERROR",
            "message": str(e),
            "rows": 0,
            "sample": ""
        }

    results = data.get("results", {})
    total_rows = 0
    sample_val = ""
    has_error = False
    error_detail = ""

    for ref_id, res in results.items():
        if "error" in res:
            has_error = True
            error_detail = res["error"]
            break

        frames = res.get("frames", [])
        if not frames:
            continue

        for frame in frames:
            data_block = frame.get("data", {})
            values = data_block.get("values", [])
            if values and len(values) > 0 and len(values[0]) > 0:
                row_count = len(values[0])
                total_rows += row_count
                if not sample_val:
                    # Pick first scalar or value
                    first_col = values[0]
                    sample_val = str(first_col[0]) if first_col else ""
                    if len(values) > 1 and len(values[1]) > 0:
                        sample_val += f", {values[1][0]}"

    if has_error:
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "ERROR",
            "message": error_detail,
            "rows": 0,
            "sample": ""
        }

    if total_rows == 0:
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "NO_DATA",
            "message": "Query returned 0 rows",
            "rows": 0,
            "sample": ""
        }

    if sample_val.lower() in ("none", "null", ""):
        return {
            "id": pid,
            "title": title,
            "type": ptype,
            "status": "NULL_VAL",
            "message": "Query returned NULL value",
            "rows": total_rows,
            "sample": "NULL"
        }

    return {
        "id": pid,
        "title": title,
        "type": ptype,
        "status": "OK",
        "message": "Data present",
        "rows": total_rows,
        "sample": sample_val[:40]
    }


def main():
    parser = argparse.ArgumentParser(description="Validate Grafana dashboard panel queries live")
    parser.add_argument("dashboard", nargs="?", default="/root/vds-servers/apps/services/grafana/dashboards/inferhub/watch.json",
                        help="Path to dashboard JSON file")
    parser.add_argument("--from", dest="time_from", default="now-24h", help="Time range from (default: now-24h)")
    parser.add_argument("--to", dest="time_to", default="now", help="Time range to (default: now)")
    parser.add_argument("--uid", dest="uid", default=None, help="Fetch dashboard by UID from API")
    parser.add_argument("--base-url", default="https://grafana.l1979.ru", help="Grafana base URL")
    args = parser.parse_args()

    repo_dir = "/root/vds-servers"
    api_key = resolve_grafana_api_key(repo_dir)
    if not api_key:
        print("ERROR: GRAFANA_API_KEY could not be resolved from env or /root/vds-servers/.env", file=sys.stderr)
        sys.exit(1)

    if args.uid:
        req = urllib.request.Request(
            f"{args.base_url}/api/dashboards/uid/{args.uid}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                dash_doc = data.get("dashboard", {})
        except Exception as e:
            print(f"ERROR fetching dashboard UID '{args.uid}': {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if not os.path.isfile(args.dashboard):
            print(f"ERROR: Dashboard file not found: {args.dashboard}", file=sys.stderr)
            sys.exit(1)
        with open(args.dashboard, "r", encoding="utf-8") as f:
            dash_doc = json.load(f)

    title = dash_doc.get("title", "Dashboard")
    uid = dash_doc.get("uid", "unknown")
    panels = get_dashboard_panels(dash_doc)

    print(f"Testing Dashboard: {title} (UID: {uid})")
    print(f"Time window: {args.time_from} -> {args.time_to}")
    print(f"Total panels found: {len(panels)}")
    print("-" * 88)
    print(f"{'ID':<4} | {'Type':<11} | {'Status':<8} | {'Rows':<6} | {'Title / Sample / Error'}")
    print("-" * 88)

    failures = 0
    for p in panels:
        res = execute_panel_query(args.base_url, api_key, p, time_from=args.time_from, time_to=args.time_to)
        status = res["status"]
        pid = res["id"]
        ptype = res["type"]
        rows = str(res["rows"])

        if status == "OK":
            status_str = "✓ OK"
            detail = f"{res['title']} → sample: {res['sample']}"
        elif status == "SKIP":
            status_str = "- SKIP"
            detail = f"{res['title']} ({res['message']})"
        elif status == "NO_DATA":
            status_str = "✗ NO DATA"
            detail = f"{res['title']} ({res['message']})"
            failures += 1
        elif status == "NULL_VAL":
            status_str = "✗ NULL"
            detail = f"{res['title']} (returned NULL)"
            failures += 1
        else:
            status_str = "✗ ERROR"
            detail = f"{res['title']}: {res['message']}"
            failures += 1

        print(f"{pid:<4} | {ptype:<11} | {status_str:<8} | {rows:<6} | {detail}")

    print("-" * 88)
    if failures > 0:
        print(f"FAILED: {failures} panel(s) returned NO DATA, NULL, or query errors.")
        sys.exit(1)
    else:
        print("SUCCESS: All active dashboard panel queries returned valid data.")
        sys.exit(0)


if __name__ == "__main__":
    main()
