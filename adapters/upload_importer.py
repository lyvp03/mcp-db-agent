from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "uploaded_db"


def infer_postgres_type(series: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        return "DOUBLE PRECISION"
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    return "TEXT"


def admin_connection_uri(database_uri: str, admin_db: str = "postgres") -> str:
    match = re.match(r"(.*/)([^/?]+)(\?.*)?$", database_uri)
    if not match:
        raise ValueError("Invalid DATABASE_URI format.")
    prefix, _db_name, suffix = match.groups()
    return f"{prefix}{admin_db}{suffix or ''}"


def database_uri_for_name(base_uri: str, db_name: str) -> str:
    match = re.match(r"(.*/)([^/?]+)(\?.*)?$", base_uri)
    if not match:
        raise ValueError("Invalid DATABASE_URI format.")
    prefix, _db_name, suffix = match.groups()
    return f"{prefix}{db_name}{suffix or ''}"


def create_database(admin_uri: str, db_name: str) -> None:
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


def create_table_from_dataframe(conn: psycopg.Connection[Any], table_name: str, df: pd.DataFrame) -> None:
    columns = []
    for column_name in df.columns:
        dtype = infer_postgres_type(df[column_name])
        columns.append(sql.SQL("{} {}").format(sql.Identifier(str(column_name)), sql.SQL(dtype)))

    with conn.cursor() as cur:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table_name)))
        cur.execute(sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(", ").join(columns)))
        if df.empty:
            return
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in df.columns)
        insert_stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table_name),
            sql.SQL(", ").join(sql.Identifier(str(col)) for col in df.columns),
            placeholders,
        )
        rows = []
        for row in df.itertuples(index=False, name=None):
            normalized_row = []
            for value in row:
                if pd.isna(value):
                    normalized_row.append(None)
                elif hasattr(value, "to_pydatetime"):
                    normalized_row.append(value.to_pydatetime())
                else:
                    normalized_row.append(value)
            rows.append(tuple(normalized_row))
        cur.executemany(insert_stmt, rows)


def import_csv(database_uri: str, uploaded_name: str, content: bytes) -> None:
    table_name = slugify(Path(uploaded_name).stem)
    df = pd.read_csv(BytesIO(content))
    with psycopg.connect(database_uri, autocommit=True) as conn:
        create_table_from_dataframe(conn, table_name, df)


def import_xlsx(database_uri: str, _uploaded_name: str, content: bytes) -> None:
    workbook = pd.read_excel(BytesIO(content), sheet_name=None)
    with psycopg.connect(database_uri, autocommit=True) as conn:
        for sheet_name, df in workbook.items():
            create_table_from_dataframe(conn, slugify(sheet_name), df)


def import_sql(database_uri: str, content: bytes) -> None:
    with psycopg.connect(database_uri, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(content.decode("utf-8"))


def import_uploaded_file(database_uri: str, uploaded_name: str, content: bytes) -> None:
    suffix = Path(uploaded_name).suffix.lower()
    if suffix == ".csv":
        import_csv(database_uri, uploaded_name, content)
        return
    if suffix in {".xlsx", ".xls"}:
        import_xlsx(database_uri, uploaded_name, content)
        return
    if suffix == ".sql":
        import_sql(database_uri, content)
        return
    raise ValueError(f"Unsupported file type: {suffix}")
