from __future__ import annotations

import ast
import os
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from adapters.mcp_tools import available_tool_names, call_tool_text, extract_table_names
from adapters.server_config import build_server_params_for_source
from core.logging_utils import debug_log
from services.registry import DatabaseSource
from services.schema_store import save_schema_snapshot
import re


def compact_table_schema(schema_name: str, table_name: str, raw_schema_str: str) -> str:
    try:
        data = ast.literal_eval(raw_schema_str)
        if isinstance(data, dict) and "columns" in data:
            cols = data.get("columns", [])
            col_strs = []
            for c in cols:
                col_name = c.get("column", c.get("name", ""))
                data_type = c.get("data_type", c.get("type", ""))
                if col_name:
                    if data_type:
                        col_strs.append(f"{col_name} {data_type}")
                    else:
                        col_strs.append(col_name)
            return f"{table_name}({', '.join(col_strs)})"
    except Exception:
        pass
    return raw_schema_str


def infer_relationships(schema_name: str, grouped_columns: dict[str, list[dict[str, Any]]]) -> list[str]:
    relationships = []
    all_columns = defaultdict(list)
    for table_name, cols in grouped_columns.items():
        for col in cols:
            all_columns[col["column"]].append((table_name, col["data_type"]))
            
    for col_name, locations in all_columns.items():
        if len(locations) < 2:
            continue
        if not col_name.endswith(("_id", "_key", "_code")):
            continue
        for i, (t1, dt1) in enumerate(locations):
            for t2, dt2 in locations[i+1:]:
                if dt1 == dt2:
                    relationships.append(f"{schema_name}.{t1}.{col_name} ↔ {schema_name}.{t2}.{col_name} (inferred)")
                    
    return relationships


async def extract_all_relationships(session: ClientSession, schema_name: str, grouped_columns: dict[str, list[dict[str, Any]]]) -> list[str]:
    relationships = []
    
    # 1. Query real Foreign Keys
    fk_sql = f"""
    SELECT
        kcu.table_name      AS source_table,
        kcu.column_name     AS source_column,
        ccu.table_name      AS target_table,
        ccu.column_name     AS target_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON tc.constraint_name = ccu.constraint_name
        AND tc.table_schema = ccu.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = '{schema_name}'
    """
    try:
        fk_result = await call_tool_text(session, "execute_sql", {"sql": fk_sql})
        fk_rows = ast.literal_eval(fk_result)
        if isinstance(fk_rows, dict):
            fk_rows = [fk_rows]
        for r in fk_rows:
            if isinstance(r, dict) and "source_table" in r:
                relationships.append(f"{schema_name}.{r['source_table']}.{r['source_column']} → {schema_name}.{r['target_table']}.{r['target_column']} (FK)")
    except Exception as exc:
        debug_log(f"Failed to parse information_schema FK rows: {exc}")

    # Track existing real FKs to avoid overlap
    existing_pairs = set()
    for rel in relationships:
        # e.g., "public.customer.cust_id -> public.subscriber.cust_id"
        parts = re.split(r' → | ↔ ', rel)
        if len(parts) >= 2:
            p1, p2 = parts[0].strip(), parts[1].split(' ')[0].strip()
            existing_pairs.add(tuple(sorted([p1, p2])))

    # 2. Heuristic inference
    inferred_rels = infer_relationships(schema_name, grouped_columns)
    for ir in inferred_rels:
        parts = re.split(r' → | ↔ ', ir)
        if len(parts) >= 2:
            p1, p2 = parts[0].strip(), parts[1].split(' ')[0].strip()
            if tuple(sorted([p1, p2])) not in existing_pairs:
                relationships.append(ir)
                
    return relationships


async def try_tool_variants(session: ClientSession, available_names: set[str], variants: list[tuple[str, dict[str, Any]]]) -> str:
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


async def introspect_via_information_schema(session: ClientSession, source: DatabaseSource) -> dict[str, Any]:
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
    table_names = [row["table_name"] for row in table_rows if isinstance(row, dict) and "table_name" in row]
    debug_log(
        f"information_schema discovered {len(table_names)} tables for `{source.source_id}`: "
        f"{table_names}"
    )

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
            {"column": row["column_name"], "data_type": row["data_type"], "is_nullable": row["is_nullable"]}
        )
    tables: dict[str, str] = {}
    for table_name in table_names:
        tables[table_name] = str({
            "basic": {"schema": schema_name, "name": table_name, "type": "table"},
            "columns": grouped_columns.get(table_name, []),
            "constraints": [],
            "indexes": [],
        })
    relationships = await extract_all_relationships(session, schema_name, grouped_columns)
        
    debug_log(
        f"Built fallback schema snapshot for `{source.source_id}` with "
        f"{len(tables)} tables and {len(relationships)} relationships"
    )
    return {"schema_name": schema_name, "table_list_text": "\n".join(table_names), "tables": tables, "relationships": relationships}


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
            debug_log(f"Discovered {len(discovered_tables)} tables for source `{source.source_id}`: {discovered_tables}")
            if not discovered_tables and "execute_sql" in tool_names:
                debug_log(f"MCP schema tools returned no tables for `{source.source_id}`; falling back to information_schema queries")
                return await introspect_via_information_schema(session, source)
            tables: dict[str, str] = {}
            grouped_columns: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
                    try:
                        data = ast.literal_eval(table_schema)
                        if isinstance(data, dict) and "columns" in data:
                            grouped_columns[table_name] = data["columns"]
                    except Exception:
                        pass
            
            relationships = await extract_all_relationships(session, source.schema_name, grouped_columns)
            return {"schema_name": source.schema_name, "table_list_text": table_list_text, "tables": tables, "relationships": relationships}


async def refresh_schema_cache(source: DatabaseSource) -> dict[str, Any]:
    debug_log(f"Refreshing schema cache for source `{source.source_id}`")
    snapshot = await introspect_source_schema(source)
    tables = snapshot.get("tables", {})
    debug_log(
        f"Refresh completed for `{source.source_id}`; preparing to save "
        f"{len(tables)} tables"
    )

    # --- Semantic Indexing: Gen keywords + Embed + Store to Qdrant ---
    keywords_data: dict[str, dict] = {}
    try:
        from google import genai
        from services.keyword_generator import generate_all_keywords
        from services.embedding_service import get_embed_client, embed_texts_async, build_embed_text
        from services.vector_store import get_qdrant_client, upsert_table_vectors, delete_collection

        # 1. Gen keywords for all tables (parallel via thread pool)
        keyword_client = genai.Client(api_key=os.getenv("MIMO_API_KEY"))
        from core.schema_graph import SchemaGraph
        temp_graph = SchemaGraph.from_snapshot(snapshot)
        compact_tables = {
            t: temp_graph.to_prompt_text([t]) for t in tables.keys()
        }
        keywords_data = await generate_all_keywords(keyword_client, compact_tables)
        debug_log(f"Generated keywords for {len(keywords_data)} tables")

        # 2. Embed all tables in parallel
        embed_client = get_embed_client()
        ordered_names = list(keywords_data.keys())
        embed_texts_list = [
            build_embed_text(name, keywords_data[name]) for name in ordered_names
        ]
        vectors = await embed_texts_async(embed_client, embed_texts_list)
        table_vectors: dict[str, list[float]] = dict(zip(ordered_names, vectors))
        debug_log(f"Embedded {len(table_vectors)} tables into vectors (parallel)")

        # 3. Store to Qdrant (delete old + upsert new)
        qdrant = get_qdrant_client()
        delete_collection(qdrant, source.source_id)
        upsert_table_vectors(qdrant, source.source_id, table_vectors, keywords_data)
        debug_log(f"Semantic index built: {len(table_vectors)} tables indexed to Qdrant")

    except Exception as exc:
        debug_log(f"Semantic indexing failed (non-fatal): {exc}")
    # --- END Semantic Indexing ---

    # Save schema cache (with keywords if available)
    save_schema_snapshot(
        source.source_id, snapshot["schema_name"],
        snapshot["table_list_text"], tables,
        snapshot.get("relationships", []),
        keywords=keywords_data,
    )
    return snapshot
