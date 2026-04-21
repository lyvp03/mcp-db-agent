from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from adapters.gemini_adapter import (
    extract_text,
    filter_tools_for_cached_schema,
    function_response_content,
    generate_with_fallback,
    generation_config,
    model_content_from_response,
    response_function_calls,
    to_gemini_tools,
)
from adapters.mcp_tools import flatten_tool_result, normalize_args
from adapters.server_config import build_server_params
from core.logging_utils import debug_log, preview_text
from core.query_context import schema_context_message
from services.registry import get_default_source, get_source
from services.schema_service import refresh_schema_cache
from services.schema_store import get_schema_snapshot


async def call_mcp_tools(
    session: ClientSession,
    conversation: list[types.Content],
    response: object,
) -> None:
    conversation.append(model_content_from_response(response))

    for function_call in response_function_calls(response):
        normalized_args = normalize_args(function_call.args)
        debug_log(
            f"Model requested tool `{function_call.name}` "
            f"with args={normalized_args}"
        )
        tool_result = await session.call_tool(
            function_call.name,
            normalized_args,
        )
        flattened = flatten_tool_result(tool_result)
        debug_log(f"Tool `{function_call.name}` completed")
        debug_log(f"Tool `{function_call.name}` result preview: {preview_text(flattened)}")
        conversation.append(
            function_response_content(
                function_call.name,
                flattened,
            )
        )


async def run_agent(user_question: str, source_id: str | None = None) -> str:
    debug_log("Loading environment variables")
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required.")

    source = get_source(source_id) if source_id else get_default_source()
    if source is None:
        raise RuntimeError(
            "No database source registered. Add one to db_registry.json first."
        )

    debug_log("Creating Gemini client")
    client = genai.Client(api_key=api_key)
    os.environ["DATABASE_URI"] = source.database_uri
    server_params = build_server_params()
    debug_log(
        f"Prepared MCP server params: command={server_params.command}, "
        f"args={server_params.args}"
    )
    conversation: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=user_question)])
    ]

    debug_log("Opening MCP stdio client")
    async with stdio_client(server_params) as (read, write):
        debug_log("MCP stdio client connected")
        async with ClientSession(read, write) as session:
            debug_log("Initializing MCP session")
            await session.initialize()
            debug_log("MCP session initialized")

            tools_result = await session.list_tools()
            debug_log(f"MCP exposed {len(tools_result.tools)} tools")

            schema_snapshot = get_schema_snapshot(source.source_id)
            if schema_snapshot is None:
                debug_log("Schema cache miss; refreshing schema snapshot")
                schema_snapshot = await refresh_schema_cache(source)

            filtered_tools = filter_tools_for_cached_schema(
                tools_result.tools,
                has_schema_cache=schema_snapshot is not None,
            )
            gemini_tools = to_gemini_tools(filtered_tools)
            config = generation_config(gemini_tools)

            if schema_snapshot:
                debug_log("Injecting schema context into conversation")
                conversation.insert(0, schema_context_message(schema_snapshot))

            while True:
                debug_log("Starting Gemini reasoning turn")
                response = generate_with_fallback(client, conversation, config)

                if not response_function_calls(response):
                    debug_log("Model returned final text response")
                    return extract_text(response)

                debug_log("Model requested MCP tools; executing tool calls")
                await call_mcp_tools(session, conversation, response)


def main() -> None:
    question = input("Ask about the database: ").strip()
    if not question:
        raise SystemExit("Question is required.")

    debug_log(f"Received user question: {question}")
    answer = asyncio.run(run_agent(question))
    print("\nAnswer:\n")
    print(answer)


def ask_database(user_question: str, source_id: str | None = None) -> str:
    return asyncio.run(run_agent(user_question, source_id=source_id))


if __name__ == "__main__":
    main()
