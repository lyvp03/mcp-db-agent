"""Determine routing strategy based on semantic search confidence."""
from core.settings import schema_search_threshold
from core.logging_utils import debug_log

def route_intent(search_results: list[dict], threshold: float = None) -> dict:
    """
    Evaluate search results and return routing decision.
    
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
    score_diff = top_score - second_score
    margin = 0.05  # Margin to consider the top score "dominant"

    debug_log(f"Routing evaluation - Top: {top_score:.4f} (`{top_table}`), Second: {second_score:.4f}. Diff: {score_diff:.4f}")

    if score_diff >= margin:
        # One table is clearly dominant -> direct injection
        debug_log(f"Decision: DIRECT routing to `{top_table}` (dominant score)")
        return {
            "strategy": "direct",
            "primary_table": top_table,
            "confidence": top_score,
            "candidates": [r.get("payload", {}).get("table_name") for r in search_results if r.get("payload")]
        }
    else:
        # Top tables have similar scores -> filter the top ones and use Graph BFS to join them
        debug_log(f"Decision: GRAPH routing (similar top scores, diff={score_diff:.4f})")
        top_candidates = [
            r.get("payload", {}).get("table_name") 
            for r in search_results 
            if r.get("payload") and (top_score - r.get("score", 0.0)) <= margin
        ]
        
        return {
            "strategy": "graph",
            "primary_table": top_table,
            "confidence": top_score,
            "candidates": top_candidates
        }
