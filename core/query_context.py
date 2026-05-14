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
    max_tables: int = 10,
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
    other_tables = [t for t in all_tables if t not in selected]

    notes: list[str] = []

    # Show selected tables' full schema
    prompt_text = graph.to_prompt_text(selected)
    if prompt_text:
        notes.append(prompt_text)

    # List remaining tables (compact, no schema) so AI knows what else exists
    if other_tables:
        notes.append(f"Other tables: {', '.join(other_tables)}")

    return "\n\n".join(notes).strip(), metadata


# ── Tokenizer singleton cache ────────────────────────────────────────
_tokenizer_cache: dict[str, "SparseTokenizer"] = {}


def _get_or_load_tokenizer(source_id: str):
    """Load sparse tokenizer from schema_cache (cached per source_id)."""
    if source_id in _tokenizer_cache:
        return _tokenizer_cache[source_id]
    try:
        from services.schema_store import get_schema_snapshot
        from services.sparse_tokenizer import SparseTokenizer
        snap = get_schema_snapshot(source_id)
        vocab = snap.get("sparse_vocabulary", {}) if snap else {}
        if not vocab:
            return None
        tok = SparseTokenizer()
        tok.vocab = {k: int(v) for k, v in vocab.items()}
        _tokenizer_cache[source_id] = tok
        return tok
    except Exception:
        return None


def _semantic_select(
    question: str,
    source_id: str | None,
    graph: SchemaGraph,
    max_tables: int,
) -> dict | None:
    """Try hybrid search (dense+sparse RRF). Falls back to dense-only if no vocabulary.

    Returns dict with tables + metadata, or None if unavailable.
    """
    if not source_id:
        return None
    try:
        from services.embedding_service import get_embed_client, embed_text
        from services.vector_store import get_qdrant_client, search_tables, hybrid_search
        from core.confidence_router import route_intent, apply_role_preference

        embed_client = get_embed_client()
        query_vector = embed_text(embed_client, question)
        qdrant = get_qdrant_client()

        # Try hybrid (dense+sparse RRF) first, fall back to dense-only
        tokenizer = _get_or_load_tokenizer(source_id)
        if tokenizer:
            sparse_idx, sparse_val = tokenizer.to_sparse(question)
            results = hybrid_search(
                qdrant, source_id, query_vector,
                sparse_idx, sparse_val, top_k=10,
            )
        else:
            results = search_tables(qdrant, source_id, query_vector, top_k=10)

        if not results:
            debug_log("Semantic search returned no results")
            return None

        # Apply table role preference (boost master/fact over lookup/satellite)
        results = apply_role_preference(results)

        # Build search_scores for UI display
        search_scores = [
            (r.get("payload", {}).get("table_name", "?"), r.get("score", 0.0))
            for r in results
        ]

        # All tables from search results (ranked by score)
        all_search_tables = [
            r.get("payload", {}).get("table_name")
            for r in results if r.get("payload")
        ]
        search_set = set(all_search_tables)

        decision = route_intent(results)
        strategy = decision["strategy"]
        candidates = decision["candidates"]

        # ── Adaptive routing: override using graph connectivity ──
        top_search = all_search_tables[:5]
        conn = graph.compute_result_connectivity(top_search)
        debug_log(
            f"Connectivity check: clusters={conn['clusters']}, "
            f"max_dist={conn['max_distance']}, complexity={conn['complexity']}"
        )

        # Override direct → graph if results span disconnected FK clusters
        if strategy == "direct" and conn["clusters"] >= 2:
            debug_log(
                f"Override: DIRECT -> GRAPH (results span {conn['clusters']} "
                f"disconnected FK clusters)"
            )
            strategy = "graph"
            decision["strategy"] = "graph"
            decision["candidates"] = all_search_tables[:5]
            candidates = decision["candidates"]

        # Adaptive max_tables based on structural complexity
        if conn["complexity"] == "complex":
            max_tables = 10
        elif conn["complexity"] == "moderate":
            max_tables = 7
        else:
            max_tables = 5
            
        # Tinh thần: Nếu đã là Graph Route (cần đi tìm path), phải nới tối đa
        # để chứa đủ bridge tables, tránh việc LLM bị thiếu bảng và phải recall tool.
        if strategy == "graph":
            max_tables = max(max_tables, 10)

        debug_log(f"Adaptive max_tables={max_tables} (complexity={conn['complexity']}, strategy={strategy})")

        if strategy == "direct":
            # Primary table is dominant — inject it + 1-hop FK neighbors from search
            primary = decision["primary_table"]
            if primary and primary in graph.tables:
                selected = [primary]
                direct_neighbors = graph._adj.get(primary, set())

                # Add search results that are direct FK neighbors (1-hop only)
                for t in all_search_tables:
                    if t not in selected and t in direct_neighbors and len(selected) < max_tables:
                        selected.append(t)

                # Inject master tables for the primary and any pulled neighbors
                selected = _inject_master_tables(selected, graph, max_tables)

                # If still room, add 1 lookup table that is direct FK neighbor
                _fill_one_lookup(selected, graph, max_tables)

                return {"tables": selected, "route_type": "direct", "search_scores": search_scores}
            return {"tables": candidates[:max_tables], "route_type": "direct", "search_scores": search_scores}

        elif strategy == "graph":
            # Multiple close candidates — BFS Steiner tree between top-3
            valid_candidates = [c for c in candidates if c in graph.tables]
            if len(valid_candidates) >= 2:
                path_tables, join_paths = graph.find_join_path(valid_candidates[:3])
                selected = list(path_tables)[:max_tables]

                # Add search results that are direct FK neighbors of the BFS path
                path_neighbors = set()
                for t in selected:
                    path_neighbors.update(graph._adj.get(t, set()))
                for t in all_search_tables:
                    if t not in selected and t in path_neighbors and len(selected) < max_tables:
                        selected.append(t)

                selected = _inject_master_tables(selected, graph, max_tables)
                _fill_one_lookup(selected, graph, max_tables)
                return {
                    "tables": selected,
                    "route_type": "graph",
                    "search_scores": search_scores,
                    "join_paths": join_paths,
                }
            elif valid_candidates:
                # Single candidate — expand 1-hop, filter to search-relevant
                all_neighbors = graph.neighbors(valid_candidates, hops=1)
                selected = valid_candidates[:]
                for n in all_neighbors:
                    if n in search_set and n not in selected and len(selected) < max_tables:
                        selected.append(n)
                selected = _inject_master_tables(selected, graph, max_tables)
                _fill_one_lookup(selected, graph, max_tables)
                return {"tables": selected, "route_type": "graph", "search_scores": search_scores, "join_paths": []}
            return {"tables": candidates[:max_tables], "route_type": "graph", "search_scores": search_scores, "join_paths": []}

        else:
            return {"tables": candidates[:max_tables], "route_type": "fallback", "search_scores": search_scores}

    except Exception as exc:
        debug_log(f"Semantic search failed, falling back to keyword: {exc}")
        return None


_LOOKUP_NAMES = {"status_codes", "countries", "currencies", "languages", "identity_types"}


def _fill_one_lookup(selected: list[str], graph: SchemaGraph, max_tables: int) -> None:
    """Add at most 1 lookup table that is a direct FK neighbor of any selected table."""
    if len(selected) >= max_tables:
        return
    selected_set = set(selected)
    for table in selected:
        for neighbor in graph._adj.get(table, set()):
            if neighbor not in selected_set and neighbor in _LOOKUP_NAMES:
                selected.append(neighbor)
                return  # only 1


def _inject_master_tables(selected: list[str], graph: SchemaGraph, max_tables: int) -> list[str]:
    """Auto-inject master parents of the currently selected tables to provide full context."""
    if len(selected) >= max_tables:
        return selected
    
    new_selected = list(selected)
    selected_set = set(selected)
    
    for table in selected:
        parents = graph.get_fk_parents(table)
        for parent in parents:
            if parent not in selected_set and len(new_selected) < max_tables:
                new_selected.append(parent)
                selected_set.add(parent)
                
    return new_selected


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

