from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from logging_utils import debug_log
from mcp_tools import available_tool_names, call_tool_text, extract_table_names
from registry import DatabaseSource
from schema_store import save_schema_snapshot
from server_config import build_server_params_for_source


async def try_tool_variants(
    session: ClientSession,
    available_names: set[str],
    variants: list[tuple[str, dict[str, Any]]],
) -> str:
    last_error: Exception | None = None

    for tool_name, args in variants:
        if tool_name not in available_names:
            continue
        try:
            return await call_tool_text(session, tool_name, args)
        except Exception as exc:
            last_error = exc
            debug_log(f"Schema refresh tool `{tool_name}` failed: {exc}")

    if last_error is not None:
        raise last_error
    return ""


async def introspect_source_schema(source: DatabaseSource) -> dict[str, Any]:
    load_dotenv()
    server_params = build_server_params_for_source(source.database_uri)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = available_tool_names(tools_result.tools)

            table_list_variants = [
                ("list_tables", {"schema_name": source.schema_name}),
                ("list_tables", {"schema": source.schema_name}),
                ("get_tables", {"schema_name": source.schema_name}),
                ("get_tables", {"schema": source.schema_name}),
                ("get_objects", {"schema_name": source.schema_name, "object_type": "table"}),
                ("get_objects", {"schema": source.schema_name, "object_type": "table"}),
            ]
            table_list_text = await try_tool_variants(session, tool_names, table_list_variants)
            discovered_tables = extract_table_names(table_list_text)
            debug_log(
                f"Discovered {len(discovered_tables)} tables for source `{source.source_id}`: "
                f"{discovered_tables}"
            )

            if not discovered_tables and "execute_sql" in tool_names:
                debug_log(
                    f"MCP schema tools returned no tables for `{source.source_id}`; "
                    "falling back to information_schema queries"
                )
                return await introspect_via_information_schema(session, source)

            tables: dict[str, str] = {}
            for table_name in discovered_tables:
                describe_variants = [
                    ("describe_table", {"schema_name": source.schema_name, "table_name": table_name}),
                    ("describe_table", {"schema": source.schema_name, "table": table_name}),
                    ("get_table_schema", {"schema_name": source.schema_name, "table_name": table_name}),
                    ("get_table_schema", {"schema": source.schema_name, "table": table_name}),
                    ("get_object_details", {"schema_name": source.schema_name, "object_name": table_name}),
                    ("get_object_details", {"schema": source.schema_name, "object_name": table_name}),
                ]
                table_schema = await try_tool_variants(session, tool_names, describe_variants)
                if table_schema:
                    tables[table_name] = table_schema

            return {
                "schema_name": source.schema_name,
                "table_list_text": table_list_text,
                "tables": tables,
            }


async def introspect_via_information_schema(
    session: ClientSession,
    source: DatabaseSource,
) -> dict[str, Any]:
    import ast

    schema_name = source.schema_name
    table_sql = f"""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = '{schema_name}'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name
    """
    table_result = await call_tool_text(session, "execute_sql", {"sql": table_sql})

    try:
        table_rows = ast.literal_eval(table_result)
        if isinstance(table_rows, dict):
            table_rows = [table_rows]
    except Exception as exc:
        debug_log(f"Failed to parse information_schema table rows: {exc}")
        table_rows = []

    table_names = [
        row["table_name"]
        for row in table_rows
        if isinstance(row, dict) and "table_name" in row
    ]

    column_sql = f"""
    SELECT table_name, column_name, data_type, is_nullable, ordinal_position
    FROM information_schema.columns
    WHERE table_schema = '{schema_name}'
    ORDER BY table_name, ordinal_position
    """
    column_result = await call_tool_text(session, "execute_sql", {"sql": column_sql})

    try:
        column_rows = ast.literal_eval(column_result)
        if isinstance(column_rows, dict):
            column_rows = [column_rows]
    except Exception as exc:
        debug_log(f"Failed to parse information_schema column rows: {exc}")
        column_rows = []

    grouped_columns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in column_rows:
        if not isinstance(row, dict):
            continue
        grouped_columns[row["table_name"]].append(
            {
                "column": row["column_name"],
                "data_type": row["data_type"],
                "is_nullable": row["is_nullable"],
            }
        )

    tables: dict[str, str] = {}
    for table_name in table_names:
        tables[table_name] = str(
            {
                "basic": {"schema": schema_name, "name": table_name, "type": "table"},
                "columns": grouped_columns.get(table_name, []),
                "constraints": [],
                "indexes": [],
            }
        )

    return {
        "schema_name": schema_name,
        "table_list_text": "\n".join(table_names),
        "tables": tables,
    }


async def refresh_schema_cache(source: DatabaseSource) -> dict[str, Any]:
    debug_log(f"Refreshing schema cache for source `{source.source_id}`")
    snapshot = await introspect_source_schema(source)
    save_schema_snapshot(
        source_id=source.source_id,
        schema_name=snapshot["schema_name"],
        table_list_text=snapshot["table_list_text"],
        tables=snapshot["tables"],
    )
    return snapshot
