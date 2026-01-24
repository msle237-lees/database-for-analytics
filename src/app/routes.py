"""
@file routes.py
@brief API routes for generic schema/table creation and CRUD operations.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pyodbc
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.db import get_db
from app.schemas import (
    CreateTableRequest,
    DbInfoResponse,
    DeleteRequest,
    DropTableRequest,
    GenericResult,
    HealthResponse,
    InsertRowRequest,
    InsertRowsRequest,
    SelectRequest,
    SelectAllRequest,
    TableInfo,
    UpdateRequest,
)

from app import queries


router = APIRouter()


def _http_400(msg: str) -> HTTPException:
    """
    @brief Build a 400 HTTPException.
    """
    return HTTPException(status_code=400, detail=msg)


def _http_500(msg: str) -> HTTPException:
    """
    @brief Build a 500 HTTPException.
    """
    return HTTPException(status_code=500, detail=msg)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """
    @brief Basic health endpoint (no DB).
    """
    return HealthResponse(status="ok")


@router.get("/health/db", response_model=HealthResponse)
def health_db(conn: pyodbc.Connection = Depends(get_db)) -> HealthResponse:
    """
    @brief DB health endpoint.
    """
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        row = cur.fetchone()
        if not row or row[0] != 1:
            raise RuntimeError("Unexpected DB response.")
        return HealthResponse(status="ok", detail="db ok")
    except Exception as e:
        raise _http_500(f"DB check failed: {e}")


@router.get("/db/info", response_model=DbInfoResponse)
def db_info() -> DbInfoResponse:
    """
    @brief Return configured DB connection info (safe subset).
    """
    return DbInfoResponse(
        server=f"{settings.db_host}:{settings.db_port}",
        database=settings.db_name,
        user=settings.db_user,
    )


# -------------------------
# Tables / Schema
# -------------------------

@router.get("/tables", response_model=List[TableInfo])
def get_tables(conn: pyodbc.Connection = Depends(get_db)) -> List[TableInfo]:
    """
    @brief List tables.
    """
    try:
        rows = queries.list_tables(conn)
        return [TableInfo(**r) for r in rows]
    except Exception as e:
        raise _http_500(f"Failed to list tables: {e}")


@router.get("/tables/{schema}/{table}", response_model=GenericResult)
def get_table_description(schema: str, table: str, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Describe a table's columns.
    """
    try:
        cols = queries.describe_table(conn, schema, table)
        return GenericResult(ok=True, message="ok", data=cols)
    except ValueError as e:
        raise _http_400(str(e))
    except Exception as e:
        raise _http_500(f"Failed to describe table: {e}")


@router.post("/tables/create", response_model=GenericResult)
def create_table(req: CreateTableRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Create an arbitrary table based on a request schema.
    """
    try:
        ok, msg = queries.create_table(
            conn=conn,
            schema=req.schema,
            table=req.table,
            columns=[c.model_dump() for c in req.columns],
            if_not_exists=req.if_not_exists,
        )
        return GenericResult(ok=ok, message=msg)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error creating table: {e}")
    except Exception as e:
        raise _http_500(f"Failed to create table: {e}")


@router.post("/tables/drop", response_model=GenericResult)
def drop_table(req: DropTableRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Drop a table.
    """
    try:
        ok, msg = queries.drop_table(conn, req.schema, req.table, if_exists=req.if_exists)
        return GenericResult(ok=ok, message=msg)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error dropping table: {e}")
    except Exception as e:
        raise _http_500(f"Failed to drop table: {e}")


# -------------------------
# Generic CRUD
# -------------------------

@router.post("/rows/insert", response_model=GenericResult)
def insert_row(req: InsertRowRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Insert a single row into any table.
    """
    try:
        n = queries.insert_row(conn, req.schema, req.table, req.row)
        return GenericResult(ok=True, message="inserted", rows_affected=n)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error inserting row: {e}")
    except Exception as e:
        raise _http_500(f"Failed to insert row: {e}")


@router.post("/rows/insert-many", response_model=GenericResult)
def insert_many(req: InsertRowsRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Insert many rows into any table.
    """
    try:
        n = queries.insert_rows(conn, req.schema, req.table, req.rows)
        return GenericResult(ok=True, message="inserted many", rows_affected=n)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error inserting rows: {e}")
    except Exception as e:
        raise _http_500(f"Failed to insert rows: {e}")


@router.post("/rows/select", response_model=GenericResult)
def select_rows(req: SelectRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Select rows from any table with simple equality filters.
    """
    try:
        data = queries.select_rows(
            conn=conn,
            schema=req.schema,
            table=req.table,
            columns=req.columns,
            where=req.where,
            limit=req.limit,
            offset=req.offset,
        )
        return GenericResult(ok=True, message="ok", data=data, rows_affected=len(data))
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error selecting rows: {e}")
    except Exception as e:
        raise _http_500(f"Failed to select rows: {e}")


@router.post("/rows/update", response_model=GenericResult)
def update_rows(req: UpdateRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Update rows in any table using equality filters.
    """
    try:
        n = queries.update_rows(conn, req.schema, req.table, req.where, req.set)
        return GenericResult(ok=True, message="updated", rows_affected=n)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error updating rows: {e}")
    except Exception as e:
        raise _http_500(f"Failed to update rows: {e}")


@router.post("/rows/delete", response_model=GenericResult)
def delete_rows(req: DeleteRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Delete rows in any table using equality filters.
    """
    try:
        n = queries.delete_rows(conn, req.schema, req.table, req.where)
        return GenericResult(ok=True, message="deleted", rows_affected=n)
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error deleting rows: {e}")
    except Exception as e:
        raise _http_500(f"Failed to delete rows: {e}")

@router.post("/rows/select-all", response_model=GenericResult)
def select_all_rows(req: SelectAllRequest, conn: pyodbc.Connection = Depends(get_db)) -> GenericResult:
    """
    @brief Select *all* rows from any table (internally batched).

    Uses a safety cap (max_rows) so you can't accidentally return millions of rows.
    """
    try:
        data = queries.select_all_rows(
            conn=conn,
            schema=req.schema,
            table=req.table,
            columns=req.columns,
            where=req.where,
            batch_size=req.batch_size,
            max_rows=req.max_rows,
        )
        return GenericResult(ok=True, message="ok", data=data, rows_affected=len(data))
    except ValueError as e:
        raise _http_400(str(e))
    except pyodbc.Error as e:
        raise _http_500(f"SQL error selecting rows: {e}")
    except Exception as e:
        raise _http_500(f"Failed to select rows: {e}")
