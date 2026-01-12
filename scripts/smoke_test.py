#!/usr/bin/env python3
"""
@file smoke_test.py
@brief End-to-end smoke test for the Generic FastAPI + SQL Server API.

Runs a small sequence of calls:
- /health
- /health/db
- create table
- insert row
- select row
- update row
- select updated row
- delete row
- drop table

Usage:
  python scripts/smoke_test.py --base-url http://127.0.0.1:8000

Optional:
  --schema dbo
  --table smoke_test_items
  --keep-table   (do not drop at the end)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: int = 10,
) -> Tuple[int, Dict[str, Any]]:
    """
    @brief Make an HTTP request and parse JSON response.
    @param method HTTP method (GET/POST/etc.).
    @param url Full URL.
    @param payload JSON body if provided.
    @param timeout_s Timeout seconds.
    @return (status_code, parsed_json_dict)
    @throws RuntimeError on non-JSON responses or request failures.
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return status, {}
            try:
                return status, json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Non-JSON response from {url}: {raw[:500]}") from e
    except urllib.error.HTTPError as e:
        # Read body for detail (often FastAPI returns JSON detail)
        body = e.read().decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return e.code, parsed
    except urllib.error.URLError as e:
        raise RuntimeError(f"Request failed to {url}: {e}") from e


def _assert(cond: bool, msg: str) -> None:
    """
    @brief Simple assertion with clean output.
    """
    if not cond:
        raise AssertionError(msg)


def _print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for Generic FastAPI SQL Server API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL (default: http://127.0.0.1:8000)")
    parser.add_argument("--schema", default="dbo", help="Schema name (default: dbo)")
    parser.add_argument("--table", default="smoke_test_items", help="Table name (default: smoke_test_items)")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout in seconds (default: 10)")
    parser.add_argument("--retries", type=int, default=10, help="Retries for DB readiness (default: 10)")
    parser.add_argument("--retry-delay", type=float, default=1.0, help="Delay between retries in seconds (default: 1.0)")
    parser.add_argument("--keep-table", action="store_true", help="Do not drop the table at the end")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    schema = args.schema
    table = args.table

    # 1) /health
    _print_step("GET /health")
    status, body = _http_json("GET", f"{base}/health", timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/health expected 200, got {status}: {body}")
    _assert(body.get("status") == "ok", f"/health expected status=ok, got: {body}")

    # 2) /health/db with retries (SQL Server can take time to start)
    _print_step("GET /health/db (with retries)")
    db_ok = False
    last_status, last_body = 0, {}
    for i in range(args.retries):
        last_status, last_body = _http_json("GET", f"{base}/health/db", timeout_s=args.timeout)
        print(f"Attempt {i+1}/{args.retries}: status={last_status}, body={last_body}")
        if last_status == 200 and last_body.get("status") == "ok":
            db_ok = True
            break
        time.sleep(args.retry_delay)

    _assert(db_ok, f"/health/db never became ready. Last: {last_status} {last_body}")

    # 3) Create table
    _print_step("POST /tables/create")
    create_payload = {
        "schema": schema,
        "table": table,
        "if_not_exists": True,
        "columns": [
            {"name": "id", "type": "INT", "nullable": False, "identity": True, "primary_key": True},
            {"name": "name", "type": "NVARCHAR(200)", "nullable": False},
            {"name": "created_at", "type": "DATETIME2", "nullable": False, "default": "SYSUTCDATETIME()"},
        ],
    }
    status, body = _http_json("POST", f"{base}/tables/create", payload=create_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/tables/create expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/tables/create expected ok=true, got: {body}")

    # 4) Insert row
    _print_step("POST /rows/insert")
    insert_payload = {"schema": schema, "table": table, "row": {"name": "SmokeTest"}}
    status, body = _http_json("POST", f"{base}/rows/insert", payload=insert_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/rows/insert expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/rows/insert expected ok=true, got: {body}")

    # 5) Select rows (find our row)
    _print_step("POST /rows/select (find inserted row)")
    select_payload = {
        "schema": schema,
        "table": table,
        "columns": ["id", "name", "created_at"],
        "where": {"name": "SmokeTest"},
        "limit": 10,
        "offset": 0,
    }
    status, body = _http_json("POST", f"{base}/rows/select", payload=select_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/rows/select expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/rows/select expected ok=true, got: {body}")

    data = body.get("data", [])
    _assert(isinstance(data, list) and len(data) >= 1, f"Expected at least one row, got: {data}")
    row_id = data[0].get("id")
    _assert(row_id is not None, f"Expected 'id' in selected row, got: {data[0]}")

    # 6) Update row by id
    _print_step("POST /rows/update (update inserted row)")
    update_payload = {
        "schema": schema,
        "table": table,
        "where": {"id": row_id},
        "set": {"name": "SmokeTestUpdated"},
    }
    status, body = _http_json("POST", f"{base}/rows/update", payload=update_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/rows/update expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/rows/update expected ok=true, got: {body}")

    # 7) Select updated row
    _print_step("POST /rows/select (verify update)")
    select_updated_payload = {
        "schema": schema,
        "table": table,
        "columns": ["id", "name"],
        "where": {"id": row_id},
        "limit": 10,
        "offset": 0,
    }
    status, body = _http_json("POST", f"{base}/rows/select", payload=select_updated_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/rows/select verify expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/rows/select verify expected ok=true, got: {body}")
    data = body.get("data", [])
    _assert(len(data) == 1, f"Expected exactly one row by id, got: {data}")
    _assert(data[0].get("name") == "SmokeTestUpdated", f"Update did not apply. Row: {data[0]}")

    # 8) Delete row
    _print_step("POST /rows/delete (delete inserted row)")
    delete_payload = {"schema": schema, "table": table, "where": {"id": row_id}}
    status, body = _http_json("POST", f"{base}/rows/delete", payload=delete_payload, timeout_s=args.timeout)
    print("Status:", status)
    print("Body:", body)
    _assert(status == 200, f"/rows/delete expected 200, got {status}: {body}")
    _assert(body.get("ok") is True, f"/rows/delete expected ok=true, got: {body}")

    # 9) Drop table (unless keep)
    if not args.keep_table:
        _print_step("POST /tables/drop (cleanup)")
        drop_payload = {"schema": schema, "table": table, "if_exists": True}
        status, body = _http_json("POST", f"{base}/tables/drop", payload=drop_payload, timeout_s=args.timeout)
        print("Status:", status)
        print("Body:", body)
        _assert(status == 200, f"/tables/drop expected 200, got {status}: {body}")
        _assert(body.get("ok") is True, f"/tables/drop expected ok=true, got: {body}")
    else:
        print("\n(Skipping drop; --keep-table set)")

    print("\n✅ SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"\n❌ SMOKE TEST FAILED: {e}")
        raise SystemExit(2)
    except Exception as e:
        print(f"\n❌ SMOKE TEST ERROR: {e}")
        raise SystemExit(3)
