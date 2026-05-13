from __future__ import annotations

import json
import re
from typing import Any

from mcp import ClientSession

from core.logging_utils import debug_log, preview_text


def flatten_tool_result(result: Any) -> str:
    chunks: list[str] = []

    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
            continue

        if hasattr(item, "model_dump"):
            chunks.append(json.dumps(item.model_dump(), ensure_ascii=False, indent=2))
            continue

        chunks.append(str(item))

    return "\n\n".join(chunks) if chunks else "Tool returned no content."


# ---------------------------------------------------------------------------
# SQL result compacting — convert verbose JSON rows to compact table format
# ---------------------------------------------------------------------------

MAX_DISPLAY_ROWS = 50


def _try_parse_rows(text: str) -> list[dict] | None:
    """Try to parse text as a JSON array of objects (typical MCP SQL result)."""
    text = text.strip()
    if not text.startswith("["):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    if data and not isinstance(data[0], dict):
        return None
    return data


def _format_value(val: Any) -> str:
    """Format a single cell value for the compact table."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, float):
        # Avoid unnecessary decimal places
        if val == int(val):
            return str(int(val))
        return f"{val:g}"
    return str(val)


def compact_sql_result(raw_text: str) -> str:
    """Convert a raw SQL result (JSON array) into schema-context-style format.

    Matches the compact inline format used by schema context injection:
        Result(plan_type, count):
          POSTPAID, 5
          PREPAID, 10
        (2 rows)

    Returns the original text unchanged if it's not a parseable JSON array.
    """
    rows = _try_parse_rows(raw_text)
    if rows is None:
        return raw_text

    if not rows:
        return "Query returned 0 rows."

    # Extract column names from the first row
    columns = list(rows[0].keys())

    # Build header in schema-context style: Result(col1, col2, ...)
    header = f"Result({', '.join(columns)}):"

    # Build data rows (truncate if too many)
    total_rows = len(rows)
    display_rows = rows[:MAX_DISPLAY_ROWS]

    lines = [header]
    for row in display_rows:
        vals = ", ".join(_format_value(row.get(col)) for col in columns)
        lines.append(f"  {vals}")

    # Row count summary
    if total_rows <= MAX_DISPLAY_ROWS:
        lines.append(f"({total_rows} rows)")
    else:
        lines.append(f"(showing {MAX_DISPLAY_ROWS} of {total_rows} rows)")

    return "\n".join(lines)


def normalize_args(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if hasattr(raw_args, "items"):
        return dict(raw_args.items())
    return dict(raw_args)


async def call_tool_text(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    debug_log(f"Calling MCP tool `{name}` with args={arguments or {}}")
    result = await session.call_tool(name, arguments or {})
    flattened = flatten_tool_result(result)
    debug_log(f"MCP tool `{name}` returned: {preview_text(flattened)}")
    return flattened


def available_tool_names(tools: list[Any]) -> set[str]:
    return {tool.name for tool in tools}


def extract_table_names(table_list_text: str) -> list[str]:
    candidates: list[str] = []
    for line in table_list_text.splitlines():
        line = line.strip()
        if not line:
            continue

        qualified = re.findall(r"\bpublic\.([A-Za-z_][A-Za-z0-9_]*)\b", line)
        named = re.findall(r"'name'\s*:\s*'([A-Za-z_][A-Za-z0-9_]*)'", line)
        named += re.findall(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', line)
        plain = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", line)

        for name in qualified + named:
            if name not in candidates:
                candidates.append(name)

        if qualified or named:
            continue

        if len(plain) == 1 and plain[0] not in {"public", "table", "tables", "schema"}:
            if plain[0] not in candidates:
                candidates.append(plain[0])

    return candidates
