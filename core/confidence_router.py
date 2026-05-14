"""Determine routing strategy based on semantic search confidence.

Supports both cosine similarity scores (dense-only) and RRF fused scores
(hybrid). The router auto-detects score distribution and adapts thresholds.
"""
from core.logging_utils import debug_log


ROLE_PRIORITY = {"master": 0, "fact": 1, "dimension": 2, "satellite": 3, "lookup": 4}


def route_intent(search_results: list[dict]) -> dict:
    """
    Evaluate search results and return routing decision.

    Works with both cosine scores (0.0–1.0) and RRF scores (typically
    clustered around 0.016–0.05 but Qdrant normalises them differently).

    Strategy:
      - "direct": one table clearly dominates → inject it + FK neighbors
      - "graph":  multiple tables are close → use BFS to find join paths
      - "fallback": no results

    Returns:
        dict: {
            "strategy": "direct" | "graph" | "fallback",
            "primary_table": str | None,
            "confidence": float,
            "candidates": list[str]
        }
    """
    if not search_results:
        return {"strategy": "fallback", "primary_table": None, "confidence": 0.0, "candidates": []}

    top_result = search_results[0]
    top_score = top_result.get("score", 0.0)
    top_table = top_result.get("payload", {}).get("table_name")

    if len(search_results) == 1:
        return {
            "strategy": "direct",
            "primary_table": top_table,
            "confidence": top_score,
            "candidates": [top_table]
        }

    second_score = search_results[1].get("score", 0.0)

    # ── Auto-detect score type and pick margin ──────────────────
    # RRF scores are relative ranks, not absolute similarities.
    # Use ratio-based margin instead of absolute difference.
    if top_score > 0:
        ratio = second_score / top_score
    else:
        ratio = 1.0

    debug_log(
        f"Routing evaluation - Top: {top_score:.4f} (`{top_table}`), "
        f"Second: {second_score:.4f}. Ratio: {ratio:.4f}"
    )

    # If the 2nd result is < 80% of the top → top is dominant → direct
    # This works for both cosine (e.g. 0.82 vs 0.65 → ratio=0.79)
    # and RRF (e.g. 1.0 vs 0.67 → ratio=0.67)
    DOMINANT_RATIO = 0.85

    if ratio < DOMINANT_RATIO:
        debug_log(f"Decision: DIRECT routing to `{top_table}` (dominant, ratio={ratio:.3f})")
        return {
            "strategy": "direct",
            "primary_table": top_table,
            "confidence": top_score,
            "candidates": [r.get("payload", {}).get("table_name") for r in search_results if r.get("payload")]
        }
    else:
        # Similar scores → collect all tables within 85% of top → graph BFS
        close_candidates = [
            r.get("payload", {}).get("table_name")
            for r in search_results
            if r.get("payload") and r.get("score", 0.0) >= top_score * DOMINANT_RATIO
        ]
        debug_log(f"Decision: GRAPH routing ({len(close_candidates)} close candidates, ratio={ratio:.3f})")
        return {
            "strategy": "graph",
            "primary_table": top_table,
            "confidence": top_score,
            "candidates": close_candidates
        }


def apply_role_preference(results: list[dict]) -> list[dict]:
    """Within close-scoring results, prefer master/fact over satellite/lookup.

    Only re-orders results within 90% of the top score — doesn't demote
    genuinely better-matching tables.
    """
    if len(results) < 2:
        return results

    top_score = results[0]["score"]
    threshold = top_score * 0.9

    close = [r for r in results if r["score"] >= threshold]
    rest = [r for r in results if r["score"] < threshold]

    close.sort(key=lambda r: (
        ROLE_PRIORITY.get(r.get("payload", {}).get("table_role", "unknown"), 5),
        -r["score"]
    ))
    return close + rest
