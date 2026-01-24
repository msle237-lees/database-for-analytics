"""
@file queries.py
@brief Generic SQL Server operations (DDL + DML) using pyodbc.

Important safety properties:
- Identifiers (schema/table/column) are validated strictly.
- Values are always passed as parameters.
- Column types for CREATE TABLE are allowlisted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pyodbc


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A pragmatic allowlist (extend later as you need).
# Supports base types and type(length[,scale]) patterns for a few types.
_ALLOWED_BASE_TYPES = {
    "INT",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "BIT",
    "FLOAT",
    "REAL",
    "DECIMAL",
    "NUMERIC",
    "MONEY",
    "SMALLMONEY",
    "DATE",
    "DATETIME",
    "DATETIME2",
    "SMALLDATETIME",
    "TIME",
    "UNIQUEIDENTIFIER",
    "NVARCHAR",
    "VARCHAR",
    "NCHAR",
    "CHAR",
    "TEXT",
    "NTEXT",
    "VARBINARY",
    "BINARY",
}

_TYPE_RE = re.compile(
    r"^(?P<base>[A-Z][A-Z0-9]*)"
    r"(\((?P<a>\d+)(,(?P<b>\d+))?\))?$"
)

_NUMERIC_FOR_IDENTITY = {"INT", "BIGINT", "SMALLINT", "TINYINT"}


def validate_identifier(name: str, label: str) -> str:
    """
    @brief Validate a SQL identifier (schema/table/column).
    @param name The identifier string.
    @param label Label used in error messages.
    @return The same name if valid.
    @throws ValueError if invalid.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid {label} '{name}'. Use letters/numbers/underscore, "
            "start with letter/underscore, no spaces or punctuation."
        )
    return name


def bracket(name: str) -> str:
    """
    @brief Wrap an identifier in SQL Server brackets.
    @param name Identifier.
    @return Bracketed identifier.
    """
    return f"[{name}]"


def validate_type(type_str: str) -> str:
    """
    @brief Validate and normalize a SQL Server type string.
    @param type_str Raw type string (e.g., 'nvarchar(255)').
    @return Uppercased normalized type string.
    @throws ValueError if not in allowlist or malformed.
    """
    t = type_str.strip().upper()
    m = _TYPE_RE.match(t)
    if not m:
        raise ValueError(f"Invalid type format: '{type_str}'")

    base = m.group("base")
    if base not in _ALLOWED_BASE_TYPES:
        raise ValueError(f"Type not allowed: '{base}'")

    # Allow base only (e.g. INT) or with parentheses (e.g. NVARCHAR(255), DECIMAL(10,2))
    a = m.group("a")
    b = m.group("b")

    if base in {"NVARCHAR", "VARCHAR", "NCHAR", "CHAR", "VARBINARY", "BINARY"}:
        # length is required for these (except you may want MAX later; add if needed)
        if a is None:
            raise ValueError(f"Type '{base}' requires a length, e.g. {base}(255).")
        n = int(a)
        if n < 1 or n > 4000 and base in {"NVARCHAR", "NCHAR"}:
            raise ValueError(f"Invalid length for {base}: {n}")
        if n < 1 or n > 8000 and base in {"VARCHAR", "CHAR", "VARBINARY", "BINARY"}:
            raise ValueError(f"Invalid length for {base}: {n}")
        return f"{base}({n})"

    if base in {"DECIMAL", "NUMERIC"}:
        if a is None:
            raise ValueError(f"Type '{base}' requires precision/scale, e.g. {base}(10,2).")
        precision = int(a)
        scale = int(b) if b is not None else 0
        if precision < 1 or precision > 38:
            raise ValueError(f"Invalid precision for {base}: {precision}")
        if scale < 0 or scale > precision:
            raise ValueError(f"Invalid scale for {base}: {scale}")
        return f"{base}({precision},{scale})"

    # Other base types should NOT carry params.
    if a is not None:
        raise ValueError(f"Type '{base}' should not include parameters.")
    return base


def fq_table(schema: str, table: str) -> str:
    """
    @brief Build a fully-qualified bracketed table name: [schema].[table]
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    return f"{bracket(schema)}.{bracket(table)}"


def table_exists(conn: pyodbc.Connection, schema: str, table: str) -> bool:
    """
    @brief Check whether a table exists.
    """
    sql = """
    SELECT 1
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
    """
    cur = conn.cursor()
    cur.execute(sql, (schema, table))
    return cur.fetchone() is not None


def list_tables(conn: pyodbc.Connection) -> List[Dict[str, str]]:
    """
    @brief List user tables (schema + name).
    """
    sql = """
    SELECT TABLE_SCHEMA, TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_SCHEMA, TABLE_NAME
    """
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    return [{"schema": r[0], "table": r[1]} for r in rows]


def describe_table(conn: pyodbc.Connection, schema: str, table: str) -> List[Dict[str, Any]]:
    """
    @brief Describe a table's columns including PK/identity/default.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")

    # Column metadata from sys catalogs
    sql = """
    SELECT
        c.name AS column_name,
        t.name AS base_type,
        c.max_length,
        c.precision,
        c.scale,
        c.is_nullable,
        c.is_identity,
        dc.definition AS default_definition,
        CASE WHEN pk_cols.column_id IS NULL THEN 0 ELSE 1 END AS is_primary_key
    FROM sys.columns c
    JOIN sys.types t ON c.user_type_id = t.user_type_id
    JOIN sys.objects o ON c.object_id = o.object_id
    LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
    LEFT JOIN (
        SELECT ic.object_id, ic.column_id
        FROM sys.indexes i
        JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
        WHERE i.is_primary_key = 1
    ) pk_cols ON pk_cols.object_id = c.object_id AND pk_cols.column_id = c.column_id
    WHERE o.type = 'U'
      AND SCHEMA_NAME(o.schema_id) = ?
      AND o.name = ?
    ORDER BY c.column_id
    """
    cur = conn.cursor()
    cur.execute(sql, (schema, table))
    rows = cur.fetchall()

    def render_type(base: str, max_length: int, precision: int, scale: int) -> str:
        b = base.upper()
        # NVARCHAR/NCHAR length is in bytes (2 bytes per char)
        if b in {"NVARCHAR", "NCHAR"}:
            length = max_length // 2 if max_length > 0 else max_length
            return f"{b}({length})" if length > 0 else b
        if b in {"VARCHAR", "CHAR", "VARBINARY", "BINARY"}:
            return f"{b}({max_length})" if max_length > 0 else b
        if b in {"DECIMAL", "NUMERIC"}:
            return f"{b}({precision},{scale})"
        return b

    out: List[Dict[str, Any]] = []
    for r in rows:
        col_name = r[0]
        base_type = r[1]
        max_length = int(r[2])
        precision = int(r[3])
        scale = int(r[4])
        is_nullable = bool(r[5])
        is_identity = bool(r[6])
        default_def = r[7]
        is_pk = bool(r[8])

        out.append(
            {
                "name": col_name,
                "type": render_type(base_type, max_length, precision, scale),
                "nullable": is_nullable,
                "is_identity": is_identity,
                "is_primary_key": is_pk,
                "default": default_def,
            }
        )
    return out


def create_table(
    conn: pyodbc.Connection,
    schema: str,
    table: str,
    columns: Sequence[Dict[str, Any]],
    if_not_exists: bool = True,
) -> Tuple[bool, str]:
    """
    @brief Create a table with arbitrary columns.
    @param conn DB connection.
    @param schema Schema name.
    @param table Table name.
    @param columns List of column defs compatible with schemas.ColumnDef.
    @param if_not_exists If true, no-op when already exists.
    @return (ok, message)
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")

    if if_not_exists and table_exists(conn, schema, table):
        return True, f"Table {schema}.{table} already exists (no-op)."

    if not columns:
        raise ValueError("At least one column is required.")

    col_sql: List[str] = []
    pk_cols: List[str] = []

    for col in columns:
        name = validate_identifier(str(col["name"]), "column")
        type_norm = validate_type(str(col["type"]))

        nullable = bool(col.get("nullable", True))
        identity = bool(col.get("identity", False))
        primary_key = bool(col.get("primary_key", False))
        default = col.get("default", None)

        base = _TYPE_RE.match(type_norm).group("base")  # type: ignore[union-attr]

        if identity and base not in _NUMERIC_FOR_IDENTITY:
            raise ValueError(f"IDENTITY is only allowed for numeric int types. Column '{name}' has '{type_norm}'.")

        parts = [bracket(name), type_norm]
        if identity:
            parts.append("IDENTITY(1,1)")
        if default is not None and str(default).strip() != "":
            # Default is an expression; keep it simple but allow reuse.
            # You can tighten this later if you want.
            parts.append(f"DEFAULT {default}")
        parts.append("NULL" if nullable else "NOT NULL")

        col_sql.append(" ".join(parts))
        if primary_key:
            pk_cols.append(bracket(name))

    pk_clause = ""
    if pk_cols:
        pk_clause = f", CONSTRAINT {bracket(f'PK_{table}')} PRIMARY KEY ({', '.join(pk_cols)})"

    sql = f"CREATE TABLE {fq_table(schema, table)} ({', '.join(col_sql)}{pk_clause});"
    cur = conn.cursor()
    cur.execute(sql)
    return True, f"Created table {schema}.{table}."


def drop_table(conn: pyodbc.Connection, schema: str, table: str, if_exists: bool = True) -> Tuple[bool, str]:
    """
    @brief Drop a table.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")

    exists = table_exists(conn, schema, table)
    if if_exists and not exists:
        return True, f"Table {schema}.{table} does not exist (no-op)."
    if not exists:
        return False, f"Table {schema}.{table} does not exist."

    sql = f"DROP TABLE {fq_table(schema, table)};"
    cur = conn.cursor()
    cur.execute(sql)
    return True, f"Dropped table {schema}.{table}."


def insert_row(conn: pyodbc.Connection, schema: str, table: str, row: Dict[str, Any]) -> int:
    """
    @brief Insert a single row.
    @return Rows affected (1).
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    if not row:
        raise ValueError("Row cannot be empty.")

    cols = [validate_identifier(k, "column") for k in row.keys()]
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(bracket(c) for c in cols)

    sql = f"INSERT INTO {fq_table(schema, table)} ({col_list}) VALUES ({placeholders});"
    params = list(row.values())

    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.rowcount


def insert_rows(conn: pyodbc.Connection, schema: str, table: str, rows: List[Dict[str, Any]]) -> int:
    """
    @brief Insert multiple rows.
    @return Total rows affected.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    if not rows:
        raise ValueError("Rows cannot be empty.")

    # All rows should share the same keys for a single INSERT statement.
    keys = list(rows[0].keys())
    if not keys:
        raise ValueError("Row cannot be empty.")
    for r in rows:
        if list(r.keys()) != keys:
            raise ValueError("All rows must have identical column keys in the same order.")

    cols = [validate_identifier(k, "column") for k in keys]
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(bracket(c) for c in cols)

    sql = f"INSERT INTO {fq_table(schema, table)} ({col_list}) VALUES ({placeholders});"
    params_seq = [list(r.values()) for r in rows]

    cur = conn.cursor()
    cur.fast_executemany = True
    cur.executemany(sql, params_seq)
    # rowcount can be -1 depending on driver settings; use len(rows) for a stable count
    return len(rows)


def select_rows(
    conn: pyodbc.Connection,
    schema: str,
    table: str,
    columns: Optional[List[str]] = None,
    where: Optional[Dict[str, Any]] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    @brief Select rows with optional equality filters, limit and offset.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")

    if columns is None or len(columns) == 0:
        select_cols = "*"
    else:
        cols = [validate_identifier(c, "column") for c in columns]
        select_cols = ", ".join(bracket(c) for c in cols)

    where_clause = ""
    params: List[Any] = []
    if where:
        parts = []
        for k, v in where.items():
            col = validate_identifier(k, "column")
            parts.append(f"{bracket(col)} = ?")
            params.append(v)
        where_clause = " WHERE " + " AND ".join(parts)

    # SQL Server paging
    sql = (
        f"SELECT {select_cols} FROM {fq_table(schema, table)}"
        f"{where_clause}"
        f" ORDER BY (SELECT NULL)"
        f" OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;"
    )
    params.extend([offset, limit])

    cur = conn.cursor()
    cur.execute(sql, params)

    col_names = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({col_names[i]: r[i] for i in range(len(col_names))})
    return out


def update_rows(conn: pyodbc.Connection, schema: str, table: str, where: Dict[str, Any], set_values: Dict[str, Any]) -> int:
    """
    @brief Update rows matching equality filters.
    @return Rows affected.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    if not where:
        raise ValueError("Update requires a non-empty 'where' filter.")
    if not set_values:
        raise ValueError("Update requires non-empty 'set' values.")

    set_parts = []
    params: List[Any] = []
    for k, v in set_values.items():
        col = validate_identifier(k, "column")
        set_parts.append(f"{bracket(col)} = ?")
        params.append(v)

    where_parts = []
    for k, v in where.items():
        col = validate_identifier(k, "column")
        where_parts.append(f"{bracket(col)} = ?")
        params.append(v)

    sql = (
        f"UPDATE {fq_table(schema, table)}"
        f" SET {', '.join(set_parts)}"
        f" WHERE {' AND '.join(where_parts)};"
    )

    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.rowcount


def delete_rows(conn: pyodbc.Connection, schema: str, table: str, where: Dict[str, Any]) -> int:
    """
    @brief Delete rows matching equality filters.
    @return Rows affected.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    if not where:
        raise ValueError("Delete requires a non-empty 'where' filter.")

    where_parts = []
    params: List[Any] = []
    for k, v in where.items():
        col = validate_identifier(k, "column")
        where_parts.append(f"{bracket(col)} = ?")
        params.append(v)

    sql = f"DELETE FROM {fq_table(schema, table)} WHERE {' AND '.join(where_parts)};"
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.rowcount

def select_all_rows(
    conn: pyodbc.Connection,
    schema: str,
    table: str,
    columns: Optional[List[str]] = None,
    where: Optional[Dict[str, Any]] = None,
    batch_size: int = 5000,
    max_rows: int = 100_000,
) -> List[Dict[str, Any]]:
    """
    @brief Select *all* rows from a table with optional equality filters.

    This function pages internally (OFFSET/FETCH) until:
      - no more rows are returned, or
      - max_rows is reached (safety cap).

    @param conn DB connection.
    @param schema Schema name.
    @param table Table name.
    @param columns Optional list of columns to return (default: all).
    @param where Optional equality filters: {col: value}.
    @param batch_size Number of rows to fetch per batch.
    @param max_rows Safety cap to prevent accidental huge responses.
    @return List of rows as dicts.
    """
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")

    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch_size must be between 1 and 5000.")
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1.")

    out: List[Dict[str, Any]] = []
    offset = 0

    while True:
        remaining = max_rows - len(out)
        if remaining <= 0:
            break

        limit = batch_size if remaining > batch_size else remaining

        chunk = select_rows(
            conn=conn,
            schema=schema,
            table=table,
            columns=columns,
            where=where,
            limit=limit,
            offset=offset,
        )

        if not chunk:
            break

        out.extend(chunk)
        offset += len(chunk)

        # If we got fewer than requested, we reached the end.
        if len(chunk) < limit:
            break

    return out
