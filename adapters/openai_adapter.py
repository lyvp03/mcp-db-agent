from __future__ import annotations

import os
from typing import Any
from openai import OpenAI

from google.genai import types
from core.logging_utils import debug_log

def _mimo_client() -> OpenAI:
    api_key = os.getenv("MIMO_API_KEY", "")
    base_url = os.getenv("MIMO_BASE_URL", "")
    if not api_key or not base_url:
        raise RuntimeError("MIMO_API_KEY and MIMO_BASE_URL must be set in .env for MiMo models")
    return OpenAI(api_key=api_key, base_url=base_url)

def _schema_to_dict(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {}
    
    if hasattr(schema, "model_dump"):
        data = schema.model_dump(exclude_none=True)
    elif hasattr(schema, "to_dict"):
        data = schema.to_dict()
    elif isinstance(schema, dict):
        data = schema.copy()
    else:
        try:
            import json
            data = json.loads(schema.model_dump_json(exclude_none=True))
        except Exception:
            data = getattr(schema, "__dict__", {"type": "object"})

    def _lower_type(d: Any) -> Any:
        if isinstance(d, dict):
            new_d = {}
            for k, v in d.items():
                if k == "type":
                    if isinstance(v, str):
                        new_d[k] = v.lower()
                    elif hasattr(v, "name"):
                        new_d[k] = v.name.lower()
                    elif hasattr(v, "value"):
                        new_d[k] = str(v.value).lower()
                    else:
                        new_d[k] = str(v).lower()
                else:
                    new_d[k] = _lower_type(v)
            return new_d
        elif isinstance(d, list):
            return [_lower_type(i) for i in d]
        return d
        
    return _lower_type(data)


ALLOWED_TOOL_NAMES = {
    "execute_sql",
    "read_query",
    "write_query",
    "query",
    "list_schemas",
    "list_tables",
    "describe_table",
    "get_table_schema"
}

def _convert_tool_to_openai(tool: types.Tool) -> list[dict[str, Any]]:
    # types.Tool has a list of function_declarations
    openai_tools = []
    if hasattr(tool, "function_declarations") and tool.function_declarations:
        for func in tool.function_declarations:
            if func.name not in ALLOWED_TOOL_NAMES:
                continue
            params = _schema_to_dict(func.parameters) if func.parameters else {"type": "object", "properties": {}}
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": func.name,
                    "description": func.description or "",
                    "parameters": params,
                }
            })
    return openai_tools

def _convert_contents_to_openai(contents: list[types.Content], system_prompt: str | None) -> list[dict[str, Any]]:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
        
    for content in contents:
        role = content.role
        if role == "model":
            role = "assistant"
            
        parts = content.parts
        if not parts:
            continue
            
        if role == "user":
            # Can be text or function_response
            text_parts = []
            for part in parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                elif getattr(part, "function_response", None):
                    # In OpenAI, function responses are a separate message role "tool"
                    fr = part.function_response
                    tool_call_id = getattr(fr, "id", None) or getattr(fr, "name", "")
                    if not tool_call_id:
                        tool_call_id = "unknown_tool_call"
                        
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": getattr(fr, "name", "tool"),
                        "content": str(fr.response.get("result", "")) if hasattr(fr, "response") and isinstance(fr.response, dict) else str(getattr(fr, "response", ""))
                    })
            if text_parts:
                messages.append({"role": "user", "content": "\n".join(text_parts)})
                
        elif role == "assistant":
            # Can be text or function_call
            text_parts = []
            tool_calls = []
            for part in parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                elif getattr(part, "function_call", None):
                    fc = part.function_call
                    tool_call_id = getattr(fc, "id", None) or getattr(fc, "name", "")
                    if not tool_call_id:
                        tool_call_id = "unknown_tool_call"
                        
                    tool_calls.append({
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": getattr(fc, "name", "tool"),
                            "arguments": str(fc.args) if isinstance(getattr(fc, "args", {}), str) else __import__("json").dumps(getattr(fc, "args", {}))
                        }
                    })
                    
            msg: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                msg["content"] = "\n".join(text_parts)
            if tool_calls:
                msg["tool_calls"] = tool_calls
            # Preserve reasoning_content for MiMo thinking mode
            reasoning = getattr(content, "_reasoning_content", None)
            if reasoning:
                msg["reasoning_content"] = reasoning
            messages.append(msg)
            
    return messages

def generate_with_mimo(model_name: str, conversation: list[types.Content], config: types.GenerateContentConfig) -> Any:
    client = _mimo_client()
    
    system_prompt = ""
    if hasattr(config, "system_instruction") and config.system_instruction:
        # system_instruction could be Content or string
        if isinstance(config.system_instruction, str):
            system_prompt = config.system_instruction
        elif hasattr(config.system_instruction, "parts"):
            system_prompt = "\n".join(p.text for p in config.system_instruction.parts if getattr(p, "text", None))
            
    messages = _convert_contents_to_openai(conversation, system_prompt)
    
    tools = []
    if hasattr(config, "tools") and config.tools:
        for t in config.tools:
            tools.extend(_convert_tool_to_openai(t))
            
    # Remove parallel tool calls if not supported, but let's try standard first
    temperature = getattr(config, "temperature", 0.0)
    if temperature is None:
        temperature = 0.0

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }
    
    if tools:
        kwargs["tools"] = tools
        
    debug_log(f"Calling MiMo generate_content with `{model_name}`")
    try:
        response = client.chat.completions.create(**kwargs)
        # Attach model name to response for later use
        setattr(response, "_model_name", model_name)
        setattr(response, "_provider", "mimo")
        return response
    except Exception as exc:
        debug_log(f"MiMo Error: {exc}")
        raise

def extract_text(response: Any) -> str:
    if not hasattr(response, "choices") or not response.choices:
        return ""
    choice = response.choices[0]
    message = choice.message
    return message.content or ""

def response_function_calls(response: Any) -> list[Any]:
    if not hasattr(response, "choices") or not response.choices:
        return []
    message = response.choices[0].message
    calls = []
    if hasattr(message, "tool_calls") and message.tool_calls:
        for tc in message.tool_calls:
            if tc.type == "function":
                # Create a mock function call object matching Gemini's structure
                import json
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                
                class MockFunctionCall:
                    def __init__(self, name, args, id_str=None):
                        self.name = name
                        self.args = args
                        self.id = id_str or name
                
                calls.append(MockFunctionCall(tc.function.name, args, tc.id))
    return calls

def model_content_from_response(response: Any) -> types.Content:
    if not hasattr(response, "choices") or not response.choices:
        return types.Content(role="model", parts=[])
        
    message = response.choices[0].message
    parts = []
    if message.content:
        parts.append(types.Part(text=message.content))
        
    if hasattr(message, "tool_calls") and message.tool_calls:
        for tc in message.tool_calls:
            if tc.type == "function":
                import json
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                # Create a mock object
                class MockFunctionCall:
                    def __init__(self, name, args, id_str=None):
                        self.name = name
                        self.args = args
                        self.id = id_str or name
                parts.append(types.Part(function_call=MockFunctionCall(tc.function.name, args, tc.id)))
                
    content = types.Content(role="model", parts=parts)
    
    # Preserve reasoning_content for MiMo thinking mode multi-turn
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        content._reasoning_content = reasoning
        
    return content


