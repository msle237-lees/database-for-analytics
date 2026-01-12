"""
@file db.py
@brief Database connection helpers for SQL Server using pyodbc.

Provides:
- build_connection_string()
- get_connection()
- FastAPI dependency get_db()
"""

from __future__ import annotations

from typing import Generator

import pyodbc

from app.config import settings


def build_connection_string() -> str:
    """
    @brief Build a SQL Server ODBC connection string.
    @return ODBC connection string.

    Notes:
    - Encrypt and TrustServerCertificate are set from env vars.
    - Timeout applies to connection attempts.
    """
    # pyodbc expects braces around the driver name if it contains spaces.
    driver = settings.db_driver
    if not driver.startswith("{"):
        driver = "{" + driver + "}"

    return (
        f"DRIVER={driver};"
        f"SERVER={settings.db_host},{settings.db_port};"
        f"DATABASE={settings.db_name};"
        f"UID={settings.db_user};"
        f"PWD={settings.db_password};"
        f"Encrypt={settings.db_encrypt};"
        f"TrustServerCertificate={settings.db_trust_server_cert};"
        f"Connection Timeout={settings.db_timeout};"
    )


def get_connection() -> pyodbc.Connection:
    """
    @brief Open a new pyodbc connection.
    @return pyodbc.Connection
    @throws pyodbc.Error on connection failure.
    """
    conn_str = build_connection_string()
    # autocommit False enables explicit transactions per request
    return pyodbc.connect(conn_str, autocommit=False)


def get_db() -> Generator[pyodbc.Connection, None, None]:
    """
    @brief FastAPI dependency that yields a DB connection.
    @return Generator yielding a connection and ensuring cleanup.

    Commits if everything is OK, rolls back on error.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
