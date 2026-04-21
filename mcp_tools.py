from __future__ import annotations

import json
from typing import Any

import re

from mcp import ClientSession

from logging_utils import debug_log, preview_text


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

        # Common output patterns:
        # - public.customers
        # - customers
        # - {"schema":"public","name":"customers"}
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
            debug_log(f"Trying schema preload tool variant `{tool_name}`")
            return await call_tool_text(session, tool_name, args)
        except Exception as exc:
            last_error = exc
            debug_log(f"Tool variant `{tool_name}` failed: {exc}")
            continue

    if last_error is not None:
        raise last_error
    return ""

