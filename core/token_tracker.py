from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnUsage:
    turn_number: int
    model_name: str
    prompt_tokens: int
    candidates_tokens: int
    thoughts_tokens: int
    total_tokens: int
    tool_calls: list[str] = field(default_factory=list)
    thinking_text: str = ""


@dataclass
class AgentResult:
    answer: str
    turns: list[TurnUsage] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_candidates_tokens: int = 0
    total_thoughts_tokens: int = 0
    total_tokens: int = 0
    model_used: str = ""
    sql_statements: list[str] = field(default_factory=list)
    tool_estimates: list[Any] = field(default_factory=list)
    # Semantic search metadata
    route_type: str = ""                      # "direct" | "graph" | "fallback" | "keyword"
    selected_tables: list[str] = field(default_factory=list)
    search_scores: list[tuple[str, float]] = field(default_factory=list)
    join_paths: list[list[str]] = field(default_factory=list)
    # Input token breakdown estimates
    schema_context_tokens: int = 0
    system_prompt_tokens: int = 0
    schema_context_text: str = ""


@dataclass
class ToolTokenInfo:
    tool_name: str
    estimated_tokens: int
    is_active: bool = True


def estimate_tool_tokens(tools: list[Any], active_names: set[str] | None = None) -> list[ToolTokenInfo]:
    """
    Heuristically estimate token usage for tool declarations.
    ~1 token per 4 characters of JSON representation.
    """
    results: list[ToolTokenInfo] = []
    
    for tool in tools:
        # Assuming tools have `name`, `description`, `inputSchema` like MCP tools
        schema_dict = {}
        if hasattr(tool, "inputSchema"):
            schema_dict = tool.inputSchema
        elif hasattr(tool, "parameters"): # Google genai format
             schema_dict = tool.parameters
             
        # Extract name and desc
        name = getattr(tool, "name", "")
        desc = getattr(tool, "description", "")
        
        # Build dictionary to simulate what is sent to the model
        tool_repr = {"name": name, "description": desc, "parameters": schema_dict}
        
        # Convert to JSON string
        try:
            # We don't need perfect serialization, just an approximation of the size
            json_str = json.dumps(tool_repr, default=str)
            estimated = max(1, len(json_str) // 4)
        except Exception:
            estimated = 50 # Fallback
            
        is_active = True
        if active_names is not None:
            is_active = name in active_names
            
        results.append(ToolTokenInfo(tool_name=name, estimated_tokens=estimated, is_active=is_active))
        
    return results
