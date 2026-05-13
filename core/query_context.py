"""Query context builder — builds schema context messages for the LLM prompt."""
from __future__ import annotations

from typing import Any

from google.genai import types

from core.schema_graph import SchemaGraph, select_tables
from core.logging_utils import debug_log


def schema_context_text(schema_snapshot: dict[str, Any]) -> str:
    """Build full schema context text (legacy — injects ALL tables)."""
    notes: list[str] = []

    table_list_text = schema_snapshot.get("table_list_text", "").strip()
    if table_list_text:
        notes.append(
            f"Tables discovered in schema `{schema_snapshot.get('schema_name', 'public')}`:\n"
            f"{table_list_text}"
        )

    for table_name, table_schema in schema_snapshot.get("tables", {}).items():
        notes.append(
            f"Schema for `{schema_snapshot.get('schema_name', 'public')}.{table_name}`:\n"
            f"{table_schema}"
        )

    relationships = schema_snapshot.get("relationships", [])
    if relationships:
        rels_text = "\n".join(f"- {rel}" for rel in relationships)
        notes.append(
            f"Relationships (Foreign Keys and Inferred):\n{rels_text}\n"
            "Use JOIN when querying across these related tables to optimize the query."
        )

    return "\n\n".join(notes).strip()


def selective_schema_context_text(
    question: str,
    schema_snapshot: dict[str, Any],
    source_id: str | None = None,
    max_tables: int = 5,
) -> tuple[str, dict]:
    """Build schema context text injecting ONLY relevant tables.

    Uses semantic search (Qdrant) first, falls back to keyword matching + BFS.
    Returns (context_text, metadata_dict).
    """
    graph = SchemaGraph.from_snapshot(schema_snapshot)
    metadata: dict[str, Any] = {
        "route_type": "keyword",
        "selected_tables": [],
        "search_scores": [],
        "join_paths": [],
    }

    # Try semantic search first
    semantic_result = _semantic_select(question, source_id, graph, max_tables)
    if semantic_result is not None:
        selected = semantic_result["tables"]
        metadata["route_type"] = semantic_result["route_type"]
        metadata["search_scores"] = semantic_result["search_scores"]
        metadata["join_paths"] = semantic_result.get("join_paths", [])
        debug_log(f"Table selection: semantic ({metadata['route_type']}) -> {selected}")
        if metadata["join_paths"]:
            for p in metadata["join_paths"]:
                debug_log(f"  BFS path: {' → '.join(p)}")
    else:
        selected = select_tables(question, graph, max_tables=max_tables)
        metadata["route_type"] = "keyword"
        debug_log(f"Table selection: keyword matching -> {selected}")

    metadata["selected_tables"] = selected

    all_tables = list(schema_snapshot.get("tables", {}).keys())

    schema_name = schema_snapshot.get("schema_name", "public")
    notes: list[str] = []

    # Always show the full table list so AI knows what exists
    notes.append(
        f"All tables in schema `{schema_name}`: {', '.join(all_tables)}"
    )

    # Show detailed schema only for selected tables
    notes.append(
        f"Detailed schema for {len(selected)} relevant table(s):"
    )
    prompt_text = graph.to_prompt_text(selected)
    if prompt_text:
        notes.append(prompt_text)

    return "\n\n".join(notes).strip(), metadata


def _semantic_select(
    question: str,
    source_id: str | None,
    graph: SchemaGraph,
    max_tables: int,
) -> dict | None:
    """Try semantic search. Returns dict with tables + metadata, or None if unavailable."""
    if not source_id:
        return None
    try:
        from services.embedding_service import get_embed_client, embed_text
        from services.vector_store import get_qdrant_client, search_tables
        from core.confidence_router import route_intent

        embed_client = get_embed_client()
        query_vector = embed_text(embed_client, question)

        qdrant = get_qdrant_client()
        results = search_tables(qdrant, source_id, query_vector, top_k=max_tables)

        if not results:
            debug_log("Semantic search returned no results")
            return None

        # Build search_scores for UI display
        search_scores = [
            (r.get("payload", {}).get("table_name", "?"), r.get("score", 0.0))
            for r in results
        ]

        decision = route_intent(results)
        strategy = decision["strategy"]
        candidates = decision["candidates"]

        if strategy == "direct":
            # High confidence: inject primary table + BFS neighbors
            primary = decision["primary_table"]
            if primary and primary in [t for t in graph.tables]:
                selected = [primary]
                # Expand with graph neighbors for JOIN context
                neighbors = graph.neighbors([primary], hops=1)
                for n in neighbors:
                    if n not in selected and len(selected) < max_tables:
                        selected.append(n)
                return {"tables": selected, "route_type": "direct", "search_scores": search_scores}
            return {"tables": candidates[:max_tables], "route_type": "direct", "search_scores": search_scores}

        elif strategy == "graph":
            # Medium confidence: use graph BFS to find bridge tables
            valid_candidates = [c for c in candidates if c in graph.tables]
            if len(valid_candidates) >= 2:
                path_tables, join_paths = graph.find_join_path(valid_candidates)
                return {
                    "tables": list(path_tables)[:max_tables],
                    "route_type": "graph",
                    "search_scores": search_scores,
                    "join_paths": join_paths,
                }
            elif valid_candidates:
                expanded = graph.neighbors(valid_candidates, hops=1)
                return {"tables": list(expanded)[:max_tables], "route_type": "graph", "search_scores": search_scores, "join_paths": []}
            return {"tables": candidates[:max_tables], "route_type": "graph", "search_scores": search_scores, "join_paths": []}

        else:
            # Fallback: return top-K candidates as-is
            return {"tables": candidates[:max_tables], "route_type": "fallback", "search_scores": search_scores}

    except Exception as exc:
        debug_log(f"Semantic search failed, falling back to keyword: {exc}")
        return None  # Fallback to keyword matching


def schema_context_message(
    schema_snapshot: dict[str, Any],
    question: str | None = None,
    source_id: str | None = None,
) -> tuple[types.Content, dict]:
    """Build a Content message with schema context.

    If *question* is provided, uses selective injection (Phase 2).
    Otherwise falls back to full injection (Phase 1).
    Returns (content_message, metadata_dict).
    """
    metadata: dict[str, Any] = {}
    if question is not None:
        context_text, metadata = selective_schema_context_text(
            question, schema_snapshot, source_id=source_id
        )
    else:
        context_text = schema_context_text(schema_snapshot)

    content = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Grounded database schema context loaded from schema cache. "
                    "Use this as the primary schema reference before answering.\n\n"
                    f"{context_text}"
                )
            )
        ],
    )
    return content, metadata

