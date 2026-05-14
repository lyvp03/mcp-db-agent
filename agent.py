from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from adapters.gemini_adapter import (
    filter_tools_for_cached_schema,
    function_response_content,
    generation_config,
    to_gemini_tools,
)
from adapters import provider_router
from adapters.mcp_tools import flatten_tool_result, normalize_args, compact_sql_result
from adapters.server_config import build_server_params
from core.logging_utils import debug_log, preview_text
from core.prompt import build_system_prompt, describe_selected_guardrails
from core.query_context import schema_context_message, schema_context_text
from core.token_tracker import AgentResult, TurnUsage, estimate_tool_tokens
from core.settings import default_model
from services.registry import get_default_source, get_source
from services.schema_service import refresh_schema_cache
from services.schema_store import get_schema_snapshot


async def call_mcp_tools(
    session: ClientSession,
    conversation: list[types.Content],
    response: object,
    turn_usage: TurnUsage,
    sql_statements: list[str],
) -> None:
    conversation.append(provider_router.model_content_from_response(response))

    for function_call in provider_router.response_function_calls(response):
        normalized_args = normalize_args(function_call.args)
        debug_log(
            f"Model requested tool `{function_call.name}` "
            f"with args={normalized_args}"
        )
        
        turn_usage.tool_calls.append(function_call.name)
        if function_call.name == "execute_sql" and "sql" in normalized_args:
            sql_statements.append(normalized_args["sql"])
            
        tool_result = await session.call_tool(
            function_call.name,
            normalized_args,
        )
        flattened = flatten_tool_result(tool_result)
        debug_log(f"Tool `{function_call.name}` completed")

        # Compact SQL results: JSON array → schema-context-style format
        if function_call.name in {"execute_sql", "read_query", "query"}:
            compacted = compact_sql_result(flattened)
            if len(compacted) < len(flattened):
                debug_log(
                    f"Compacted SQL result: {len(flattened)} → {len(compacted)} chars "
                    f"({100 - len(compacted) * 100 // max(1, len(flattened))}% saved)"
                )
            flattened = compacted

        debug_log(f"Tool `{function_call.name}` result preview: {preview_text(flattened)}")
        conversation.append(
            function_response_content(
                function_call.name,
                flattened,
                id_str=getattr(function_call, "id", None)
            )
        )


async def run_agent(user_question: str, source_id: str | None = None, model_name: str | None = None) -> AgentResult:
    debug_log("Loading environment variables")
    load_dotenv()

    if not model_name:
        model_name = default_model()

    api_key = os.getenv("GEMINI_API_KEY")
    client = None
    if api_key:
        client = genai.Client(api_key=api_key)

    source = get_source(source_id) if source_id else get_default_source()
    if source is None:
        raise RuntimeError(
            "No database source registered. Add one to db_registry.json first."
        )

    os.environ["DATABASE_URI"] = source.database_uri
    server_params = build_server_params()
    debug_log(
        f"Prepared MCP server params: command={server_params.command}, "
        f"args={server_params.args}"
    )
    conversation: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=user_question)])
    ]

    agent_result = AgentResult(answer="", model_used=model_name)

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
            
            active_names = {t.name for t in filtered_tools}
            agent_result.tool_estimates = estimate_tool_tokens(tools_result.tools, active_names)
            
            selected_guardrails = describe_selected_guardrails(user_question, schema_snapshot)
            debug_log(f"Selected prompt sections: {selected_guardrails}")
            system_prompt = build_system_prompt(user_question, schema_snapshot)
            config = generation_config(gemini_tools, system_prompt)
            if client and not model_name.startswith("mimo"):
                try:
                    exact_sys = client.models.count_tokens(model=model_name, contents=system_prompt)
                    agent_result.system_prompt_tokens = exact_sys.total_tokens
                except Exception:
                    agent_result.system_prompt_tokens = max(1, len(system_prompt) // 4)
            elif model_name.startswith("mimo"):
                try:
                    import tiktoken
                    enc = tiktoken.get_encoding("cl100k_base")
                    agent_result.system_prompt_tokens = len(enc.encode(system_prompt))
                except ImportError:
                    agent_result.system_prompt_tokens = max(1, len(system_prompt) // 4)
            else:
                agent_result.system_prompt_tokens = max(1, len(system_prompt) // 4)

            if schema_snapshot:
                debug_log("Injecting schema context into conversation")
                context_msg, search_meta = schema_context_message(schema_snapshot, question=user_question, source_id=source.source_id)
                conversation.insert(0, context_msg)
                agent_result.route_type = search_meta.get("route_type", "")
                agent_result.selected_tables = search_meta.get("selected_tables", [])
                agent_result.search_scores = search_meta.get("search_scores", [])
                agent_result.join_paths = search_meta.get("join_paths", [])
                # Estimate schema context tokens from the injected text
                schema_text = context_msg.parts[0].text if context_msg.parts else ""
                full_text = schema_context_text(schema_snapshot)
                
                if client and not model_name.startswith("mimo"):
                    try:
                        # Fetch exact token count using Gemini API
                        exact_full = client.models.count_tokens(model=model_name, contents=full_text)
                        exact_inj = client.models.count_tokens(model=model_name, contents=schema_text)
                        agent_result.full_schema_context_tokens = exact_full.total_tokens
                        agent_result.schema_context_tokens = exact_inj.total_tokens
                    except Exception as exc:
                        debug_log(f"Token count API failed: {exc}")
                        agent_result.full_schema_context_tokens = max(1, len(full_text) // 4)
                        agent_result.schema_context_tokens = max(1, len(schema_text) // 4)
                elif model_name.startswith("mimo"):
                    try:
                        import tiktoken
                        enc = tiktoken.get_encoding("cl100k_base")
                        agent_result.full_schema_context_tokens = len(enc.encode(full_text))
                        agent_result.schema_context_tokens = len(enc.encode(schema_text))
                    except ImportError:
                        agent_result.full_schema_context_tokens = max(1, len(full_text) // 4)
                        agent_result.schema_context_tokens = max(1, len(schema_text) // 4)
                else:
                    agent_result.full_schema_context_tokens = max(1, len(full_text) // 4)
                    agent_result.schema_context_tokens = max(1, len(schema_text) // 4)
                    
                agent_result.schema_context_text = schema_text

            turn_count = 0
            while True:
                turn_count += 1
                debug_log(f"Starting reasoning turn {turn_count} with `{model_name}`")
                response = provider_router.generate_content(client, model_name, conversation, config)
                
                usage = provider_router.extract_usage(response)
                turn = TurnUsage(
                    turn_number=turn_count,
                    model_name=model_name,
                    prompt_tokens=usage["prompt_tokens"],
                    candidates_tokens=usage["candidates_tokens"],
                    thoughts_tokens=usage["thoughts_tokens"],
                    total_tokens=usage["total_tokens"],
                    thinking_text=usage["thinking_text"],
                )
                
                agent_result.total_prompt_tokens += turn.prompt_tokens
                agent_result.total_candidates_tokens += turn.candidates_tokens
                agent_result.total_thoughts_tokens += turn.thoughts_tokens
                agent_result.total_tokens += turn.total_tokens

                if not provider_router.response_function_calls(response):
                    debug_log("Model returned final text response")
                    agent_result.answer = provider_router.extract_text(response)
                    agent_result.turns.append(turn)
                    return agent_result

                debug_log("Model requested MCP tools; executing tool calls")
                await call_mcp_tools(session, conversation, response, turn, agent_result.sql_statements)
                agent_result.turns.append(turn)


def main() -> None:
    question = input("Ask about the database: ").strip()
    if not question:
        raise SystemExit("Question is required.")

    debug_log(f"Received user question: {question}")
    result = asyncio.run(run_agent(question))
    print("\nAnswer:\n")
    print(result.answer)
    print(f"\nTokens used: {result.total_tokens}")


def ask_database(user_question: str, source_id: str | None = None, model_name: str | None = None) -> Any:
    return asyncio.run(run_agent(user_question, source_id=source_id, model_name=model_name))


if __name__ == "__main__":
    main()
