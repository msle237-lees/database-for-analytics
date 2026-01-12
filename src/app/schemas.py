"""
@file schemas.py
@brief Pydantic models for generic DDL/DML operations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    detail: Optional[str] = None


class DbInfoResponse(BaseModel):
    server: str
    database: str
    user: str


class ColumnDef(BaseModel):
    """
    @brief Column definition for CREATE TABLE.
    """

    name: str = Field(..., description="Column name (identifier rules apply).")
    type: str = Field(..., description="SQL Server type (allowlisted). e.g., INT, NVARCHAR(255)")
    nullable: bool = Field(True, description="Whether column is nullable.")
    identity: bool = Field(False, description="Whether column is IDENTITY(1,1). Only valid for numeric types.")
    primary_key: bool = Field(False, description="Whether column participates in PRIMARY KEY.")
    default: Optional[str] = Field(None, description="Default expression, e.g., GETDATE() or 0 or 'abc'.")


class CreateTableRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    if_not_exists: bool = Field(True, description="If true, does nothing if table already exists.")
    columns: List[ColumnDef]


class DropTableRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    if_exists: bool = Field(True, description="If true, does nothing if missing.")


class InsertRowRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    row: Dict[str, Any] = Field(..., description="Column->value mapping.")


class InsertRowsRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    rows: List[Dict[str, Any]] = Field(..., description="List of column->value mappings.")


class SelectRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    columns: Optional[List[str]] = Field(None, description="Columns to return (default: all).")
    where: Optional[Dict[str, Any]] = Field(None, description="Equality filters: {col: value}.")
    limit: int = Field(100, ge=1, le=5000, description="Max rows returned.")
    offset: int = Field(0, ge=0, description="Rows to skip.")


class UpdateRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    where: Dict[str, Any] = Field(..., description="Equality match filters to select rows.")
    set: Dict[str, Any] = Field(..., description="Column updates.")


class DeleteRequest(BaseModel):
    schema: str = Field("dbo", description="Schema name.")
    table: str = Field(..., description="Table name.")
    where: Dict[str, Any] = Field(..., description="Equality match filters to select rows.")


class GenericResult(BaseModel):
    ok: bool
    message: str
    rows_affected: Optional[int] = None
    data: Optional[Any] = None


class TableInfo(BaseModel):
    schema: str
    table: str


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    is_identity: bool
    is_primary_key: bool
    default: Optional[str] = None
