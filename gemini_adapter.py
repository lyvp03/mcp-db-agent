from __future__ import annotations

import os
import re
import time
from typing import Any

from google import genai
from google.genai import errors, types

from logging_utils import debug_log
from prompt import load_system_prompt

SCHEMA_TOOL_NAMES = {
    "list_tables",
    "get_tables",
    "get_objects",
    "describe_table",
    "get_table_schema",
    "get_object_details",
}


def sanitize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"additional_properties", "additionalProperties", "$schema", "default"}:
                continue
            sanitized[key] = sanitize_schema(item)

        if sanitized.get("type") == "object":
            sanitized.setdefault("properties", {})

        return sanitized

    if isinstance(value, list):
        return [sanitize_schema(item) for item in value]

    return value


def to_gemini_tools(tools: list[Any]) -> list[types.Tool]:
    declarations: list[types.FunctionDeclaration] = []
    for tool in tools:
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description or "",
                parameters=sanitize_schema(tool.inputSchema),
            )
        )

    return [types.Tool(function_declarations=declarations)]


def filter_tools_for_cached_schema(tools: list[Any], has_schema_cache: bool) -> list[Any]:
    if not has_schema_cache:
        return tools

    filtered = [tool for tool in tools if tool.name not in SCHEMA_TOOL_NAMES]
    debug_log(
        f"Schema cache present; filtered tools from {len(tools)} to {len(filtered)} "
        f"by removing schema introspection tools"
    )
    return filtered


def generation_config(mcp_tools: list[types.Tool]) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=0,
        tools=mcp_tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        system_instruction=load_system_prompt(),
    )


def model_candidates() -> list[str]:
    primary = os.getenv("MODEL", "gemini-2.5-flash")
    fallbacks = os.getenv("MODEL_FALLBACKS", "")

    models = [primary]
    for item in fallbacks.split(","):
        name = item.strip()
        if name and name not in models:
            models.append(name)
    return models


def retry_delay_seconds(exc: errors.ClientError) -> float | None:
    text = str(exc)
    patterns = [
        r"retryDelay':\s*'(\d+(?:\.\d+)?)s'",
        r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"',
        r"Please retry in (\d+(?:\.\d+)?)s",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))

    response_json = getattr(exc, "response_json", None)
    if not isinstance(response_json, dict):
        return None

    details = response_json.get("error", {}).get("details", [])
    if not isinstance(details, list):
        return None

    for detail in details:
        if not isinstance(detail, dict):
            continue
        retry_delay = detail.get("retryDelay")
        if not isinstance(retry_delay, str):
            continue
        match = re.fullmatch(r"(\d+)(?:\.(\d+))?s", retry_delay.strip())
        if not match:
            continue
        whole = int(match.group(1))
        fraction = match.group(2) or ""
        fraction_value = float(f"0.{fraction}") if fraction else 0.0
        return whole + fraction_value

    return None


def generate_with_fallback(
    client: genai.Client,
    conversation: list[types.Content],
    config: types.GenerateContentConfig,
) -> Any:
    last_error: Exception | None = None

    for model_name in model_candidates():
        debug_log(f"Trying model `{model_name}`")
        for attempt in range(3):
            try:
                debug_log(
                    f"Calling Gemini generate_content with `{model_name}` "
                    f"(attempt {attempt + 1}/3, conversation items={len(conversation)})"
                )
                return client.models.generate_content(
                    model=model_name,
                    contents=conversation,
                    config=config,
                )
            except errors.ServerError as exc:
                last_error = exc
                debug_log(f"ServerError from `{model_name}`: {exc}")
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            except errors.ClientError as exc:
                last_error = exc
                debug_log(f"ClientError from `{model_name}`: {exc}")
                delay = retry_delay_seconds(exc)
                if delay is not None and attempt < 2:
                    debug_log(
                        f"Retrying `{model_name}` after rate limit backoff: {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Gemini model could generate a response.")


def extract_text(response: Any) -> str:
    chunks: list[str] = []

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue

        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)

    if chunks:
        return "\n".join(chunks).strip()

    text = getattr(response, "text", None)
    return text.strip() if text else ""


def response_function_calls(response: Any) -> list[Any]:
    calls: list[Any] = []

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue

        for part in getattr(content, "parts", []) or []:
            function_call = getattr(part, "function_call", None)
            if function_call:
                calls.append(function_call)

    return calls


def model_content_from_response(response: Any) -> types.Content:
    parts: list[types.Part] = []

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue

        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                parts.append(types.Part(text=text))
                continue

            function_call = getattr(part, "function_call", None)
            if function_call:
                parts.append(types.Part(function_call=function_call))

    return types.Content(role="model", parts=parts)


def function_response_content(name: str, payload: str) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=name,
                    response={"result": payload},
                )
            )
        ],
    )
