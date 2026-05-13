from __future__ import annotations

from typing import Any
from google.genai import types

from adapters import gemini_adapter
from adapters import openai_adapter

def detect_provider(model_name: str) -> str:
    if model_name.lower().startswith("mimo"):
        return "mimo"
    return "gemini"

def _is_mimo(response: Any) -> bool:
    return getattr(response, "_provider", "") == "mimo"

def generate_content(client: Any, model_name: str, conversation: list[types.Content], config: types.GenerateContentConfig) -> Any:
    provider = detect_provider(model_name)
    if provider == "mimo":
        return openai_adapter.generate_with_mimo(model_name, conversation, config)
    else:
        # Pass model_override directly via kwargs or we can temporarily inject it.
        # But wait, gemini_adapter.generate_with_fallback currently doesn't take model override.
        # Let's fix gemini_adapter.generate_with_fallback directly!
        return gemini_adapter.generate_with_fallback(client, conversation, config, model_override=model_name)

def extract_text(response: Any) -> str:
    if _is_mimo(response):
        return openai_adapter.extract_text(response)
    return gemini_adapter.extract_text(response)

def response_function_calls(response: Any) -> list[Any]:
    if _is_mimo(response):
        return openai_adapter.response_function_calls(response)
    return gemini_adapter.response_function_calls(response)

def model_content_from_response(response: Any) -> types.Content:
    if _is_mimo(response):
        return openai_adapter.model_content_from_response(response)
    return gemini_adapter.model_content_from_response(response)

def extract_usage(response: Any) -> dict[str, Any]:
    # Returns standardized token counts and thinking text
    result = {
        "prompt_tokens": 0,
        "candidates_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 0,
        "thinking_text": ""
    }
    
    if _is_mimo(response):
        if hasattr(response, "usage") and response.usage:
            result["prompt_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            result["candidates_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
            result["total_tokens"] = getattr(response.usage, "total_tokens", 0) or 0
        # Check reasoning content if available
        if hasattr(response, "choices") and response.choices:
            message = response.choices[0].message
            if hasattr(message, "reasoning_content") and message.reasoning_content:
                result["thinking_text"] = message.reasoning_content
    else:
        # Gemini
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            result["prompt_tokens"] = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            result["candidates_tokens"] = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            result["thoughts_tokens"] = getattr(response.usage_metadata, "thoughts_token_count", 0) or 0
            result["total_tokens"] = getattr(response.usage_metadata, "total_token_count", 0) or 0
            
        # Thinking text is in candidate parts where thought=True
        thinking_text = ""
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content: continue
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "thought", False):
                    thinking_text += getattr(part, "text", "")
        result["thinking_text"] = thinking_text.strip()
        
    return result

