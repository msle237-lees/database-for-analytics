#!/usr/bin/env python3
"""
@file csv_to_table.py
@brief Create a SQL Server table from a CSV file by calling the Generic FastAPI API,
       then bulk insert rows using the API.

This script:
- Reads a CSV header and rows
- Infers SQL Server column types
- Creates a table via POST /tables/create
- Inserts data via POST /rows/insert-many in batches

Usage:
  python scripts/csv_to_table.py \
    --base-url http://127.0.0.1:8000 \
    --csv ./data/my_dataset.csv \
    --schema dbo \
    --table my_dataset \
    --if-not-exists \
    --batch-size 500

Notes:
- Column names are normalized to safe SQL identifiers.
- Values are sent as JSON; empty strings become null (optional).
- Type inference is heuristic; you can override types with --override "col=NVARCHAR(500)" etc.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# HTTP helper
# -----------------------------

def http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: int = 30,
) -> Tuple[int, Dict[str, Any]]:
    """
    @brief Make an HTTP request and parse JSON response.
    @param method HTTP method.
    @param url Full URL.
    @param payload JSON body if provided.
    @param timeout_s Timeout in seconds.
    @return (status_code, parsed_json)
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
            except json.JSONDecodeError:
                return status, {"raw": raw}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return e.code, parsed


def die(msg: str, code: int = 2) -> None:
    """
    @brief Print error and exit.
    """
    print(f"\nERROR: {msg}")
    raise SystemExit(code)


# -----------------------------
# Identifier normalization
# -----------------------------

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def normalize_identifier(raw: str) -> str:
    """
    @brief Convert an arbitrary CSV column header into a safe SQL identifier.
    @param raw Original header string.
    @return Normalized identifier.
    """
    s = (raw or "").strip()
    if not s:
        s = "col"

    # Replace spaces/punct with underscore
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s)

    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s)

    # Trim underscores
    s = s.strip("_")

    # Ensure starts with letter or underscore
    if not s:
        s = "col"
    if not re.match(r"^[A-Za-z_]", s):
        s = "_" + s

    # Truncate to a reasonable identifier length
    s = s[:128]

    # Final sanity
    if not _IDENT_RE.match(s):
        # fallback
        s = "col_" + str(abs(hash(raw)) % 10_000_000)

    return s


def make_unique(names: List[str]) -> List[str]:
    """
    @brief Ensure column names are unique after normalization.
    """
    seen: Dict[str, int] = {}
    out: List[str] = []
    for n in names:
        base = n
        if base not in seen:
            seen[base] = 0
            out.append(base)
            continue
        seen[base] += 1
        out.append(f"{base}_{seen[base]}")
    return out


# -----------------------------
# Type inference
# -----------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

def try_parse_datetime(s: str) -> bool:
    """
    @brief Very small set of datetime checks (heuristic).
    """
    s = s.strip()
    if not s:
        return False
    # Common ISO-ish formats
    fmts = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for f in fmts:
        try:
            datetime.strptime(s, f)
            return True
        except ValueError:
            pass
    return False


class ColStats:
    """
    @brief Tracks observed values to infer a SQL type.
    """

    def __init__(self) -> None:
        self.seen_nonnull = 0
        self.maybe_int = True
        self.maybe_float = True
        self.maybe_datetime = True
        self.max_len = 0

    def observe(self, val: Optional[str]) -> None:
        if val is None:
            return
        v = val.strip()
        if v == "":
            return

        self.seen_nonnull += 1
        self.max_len = max(self.max_len, len(v))

        if self.maybe_int and not _INT_RE.match(v):
            self.maybe_int = False

        if self.maybe_float and not _FLOAT_RE.match(v):
            self.maybe_float = False

        if self.maybe_datetime and not try_parse_datetime(v):
            self.maybe_datetime = False

    def infer_sql_type(self) -> str:
        """
        @brief Infer a SQL Server type from observed values.
        @return SQL type string compatible with your API allowlist.
        """
        # If no data: default string
        if self.seen_nonnull == 0:
            return "NVARCHAR(255)"

        if self.maybe_int:
            return "BIGINT"  # safest int default

        if self.maybe_float:
            return "FLOAT"

        if self.maybe_datetime:
            return "DATETIME2"

        # String fallback - size bucketed
        # Keep within NVARCHAR limits. For huge fields, cap.
        if self.max_len <= 50:
            return "NVARCHAR(50)"
        if self.max_len <= 255:
            return "NVARCHAR(255)"
        if self.max_len <= 1000:
            return "NVARCHAR(1000)"
        if self.max_len <= 2000:
            return "NVARCHAR(2000)"

        # Cap at 4000 for NVARCHAR in our API allowlist rules
        return "NVARCHAR(4000)"


def parse_overrides(overrides: List[str]) -> Dict[str, str]:
    """
    @brief Parse overrides like ["colA=NVARCHAR(500)", "colB=INT"].
    """
    out: Dict[str, str] = {}
    for o in overrides:
        if "=" not in o:
            die(f"Invalid override '{o}'. Use col=TYPE")
        k, v = o.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k or not v:
            die(f"Invalid override '{o}'. Use col=TYPE")
        out[k] = v
    return out


# -----------------------------
# CSV loading + pipeline
# -----------------------------

def read_csv_sample(csv_path: Path, sample_rows: int) -> Tuple[List[str], List[Dict[str, Optional[str]]], List[ColStats]]:
    """
    @brief Read header + up to sample_rows rows for type inference.
    @return (normalized_headers, sampled_rows, stats_per_col)
    """
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            die("CSV is empty.")

        norm_headers = make_unique([normalize_identifier(h) for h in raw_headers])
        stats = [ColStats() for _ in norm_headers]
        sampled: List[Dict[str, Optional[str]]] = []

        for i, row in enumerate(reader):
            if i >= sample_rows:
                break
            # pad or trim to header length
            row = (row + [""] * len(norm_headers))[: len(norm_headers)]
            d: Dict[str, Optional[str]] = {}
            for idx, col in enumerate(norm_headers):
                val = row[idx] if idx < len(row) else ""
                d[col] = val
                stats[idx].observe(val)
            sampled.append(d)

        return norm_headers, sampled, stats


def iter_csv_rows(csv_path: Path, headers: List[str]) -> Any:
    """
    @brief Stream all rows as dicts with the provided headers.
    """
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        # skip header
        try:
            next(reader)
        except StopIteration:
            return

        for row in reader:
            row = (row + [""] * len(headers))[: len(headers)]
            d: Dict[str, Optional[str]] = {}
            for i, h in enumerate(headers):
                d[h] = row[i]
            yield d


def coerce_row(row: Dict[str, Optional[str]], col_types: Dict[str, str], empty_to_null: bool) -> Dict[str, Any]:
    """
    @brief Convert row string values into JSON-friendly values based on inferred SQL types.
    Invalid values for numeric columns become NULL instead of causing insert failures.
    """
    out: Dict[str, Any] = {}

    for col, raw in row.items():
        t = col_types[col].upper()

        if raw is None:
            out[col] = None
            continue

        v = raw.strip()

        # Empty handling
        if v == "":
            out[col] = None if empty_to_null else ""
            continue

        # Integers
        if t in {"INT", "BIGINT", "SMALLINT", "TINYINT"}:
            try:
                # Some datasets use "1.0" for whole numbers — handle that safely
                if "." in v or "e" in v.lower():
                    f = float(v)
                    out[col] = int(f) if f.is_integer() else None
                else:
                    out[col] = int(v)
            except ValueError:
                out[col] = None
            continue

        # Floats
        if t in {"FLOAT", "REAL"}:
            try:
                out[col] = float(v)
            except ValueError:
                out[col] = None
            continue

        # DECIMAL/NUMERIC/MONEY: keep numeric strings if possible, else NULL
        if t.startswith("DECIMAL") or t.startswith("NUMERIC") or t in {"MONEY", "SMALLMONEY"}:
            try:
                float(v)  # validate
                out[col] = v
            except ValueError:
                out[col] = None
            continue

        # Date/time types: keep string (SQL Server will parse common formats)
        out[col] = v

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Create SQL Server table from CSV via the Generic FastAPI API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--schema", default="dbo", help="Schema name (default: dbo)")
    parser.add_argument("--table", required=True, help="Target table name")
    parser.add_argument("--if-not-exists", action="store_true", help="Do not fail if table already exists (no-op)")
    parser.add_argument("--sample-rows", type=int, default=200, help="Rows to sample for type inference (default: 200)")
    parser.add_argument("--batch-size", type=int, default=500, help="Insert batch size (default: 500)")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds (default: 60)")
    parser.add_argument("--empty-to-null", action="store_true", help="Convert empty strings to null")
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Override column type: --override col=NVARCHAR(500) (can be used multiple times)",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.exists():
        die(f"CSV not found: {csv_path}")

    # 0) API health
    status, body = http_json("GET", f"{base}/health", timeout_s=args.timeout)
    if status != 200:
        die(f"API not healthy: {status} {body}")

    # 1) Read sample to infer headers/types
    print(f"Reading CSV sample for inference: {csv_path}")
    headers, sampled, stats = read_csv_sample(csv_path, sample_rows=args.sample_rows)
    overrides = parse_overrides(args.override)

    inferred_types: Dict[str, str] = {}
    for h, st in zip(headers, stats):
        inferred_types[h] = st.infer_sql_type()

    # Apply overrides (allow using original or normalized names; we only store normalized)
    for k, v in overrides.items():
        norm_k = normalize_identifier(k)
        if norm_k not in inferred_types:
            die(f"Override column '{k}' (normalized to '{norm_k}') not found in CSV columns: {headers}")
        inferred_types[norm_k] = v.strip()

    # 2) Create table payload
    cols_payload: List[Dict[str, Any]] = []
    for h in headers:
        cols_payload.append(
            {
                "name": h,
                "type": inferred_types[h],
                "nullable": True,
                "identity": False,
                "primary_key": False,
            }
        )

    create_payload = {
        "schema": args.schema,
        "table": normalize_identifier(args.table),
        "if_not_exists": bool(args.if_not_exists),
        "columns": cols_payload,
    }

    print("\nCreating table:")
    print(f"  schema: {create_payload['schema']}")
    print(f"  table : {create_payload['table']}")
    print("  columns:")
    for c in cols_payload:
        print(f"    - {c['name']}: {c['type']}")

    status, body = http_json("POST", f"{base}/tables/create", payload=create_payload, timeout_s=args.timeout)
    if status != 200 or not body.get("ok", False):
        die(f"Table create failed: HTTP {status} {body}")
    print("Table create:", body.get("message", "ok"))

    # 3) Insert all rows in batches
    print("\nInserting rows...")
    batch: List[Dict[str, Any]] = []
    total = 0

    for raw_row in iter_csv_rows(csv_path, headers):
        row = coerce_row(raw_row, inferred_types, empty_to_null=args.empty_to_null)
        batch.append(row)
        if len(batch) >= args.batch_size:
            payload = {"schema": args.schema, "table": create_payload["table"], "rows": batch}
            status, body = http_json("POST", f"{base}/rows/insert-many", payload=payload, timeout_s=args.timeout)
            if status != 200 or not body.get("ok", False):
                die(f"Insert-many failed at row {total}: HTTP {status} {body}")
            total += len(batch)
            print(f"  inserted {total}")
            batch = []

    # Flush remainder
    if batch:
        payload = {"schema": args.schema, "table": create_payload["table"], "rows": batch}
        status, body = http_json("POST", f"{base}/rows/insert-many", payload=payload, timeout_s=args.timeout)
        if status != 200 or not body.get("ok", False):
            die(f"Final insert-many failed at row {total}: HTTP {status} {body}")
        total += len(batch)

    print(f"\n✅ Done. Inserted {total} rows into {args.schema}.{create_payload['table']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
