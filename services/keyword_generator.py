"""Generate user-facing keywords for each table using LLM."""
import os
import re
import json
import yaml
import hashlib
import asyncio
import time
from typing import Any
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai import types
from core.settings import keyword_gen_model
from core.logging_utils import debug_log
from adapters.provider_router import generate_content, extract_text, extract_usage

ABBREVIATIONS = {
    "cdr": "call detail record",
    "cust": "customer",
    "txn": "transaction",
    "rm": "roaming",
    "kpi": "key performance indicator"
}

GENERIC_BLACKLIST = {
    "thống kê", "phân tích", "kiểm tra", "tra cứu", "tìm kiếm", 
    "dữ liệu", "thông tin", "bảng", "hệ thống", "quản lý"
}

KEYWORD_PROMPT = """You are an expert Data Architect generating semantic retrieval tags for database tables.

Your goal is to infer the core BUSINESS IDENTITY of the table.

### ABBREVIATIONS KNOWLEDGE BASE
{abbreviations}

### RULES
1. Abstract Table Identity: Synthesize columns to understand the real-world business entity.
2. Natural Vietnamese: Use natural business terms. Avoid literal column translations.
3. Keep Crucial English Aliases: Use the abbreviations knowledge base.
4. 5-8 High-Confidence Concepts: Only generate the most defining concepts. Do not generate filler keywords.
5. Split by Semantic Type: Categorize keywords into domain, activity, usage, aliases.

### EXAMPLES

GOOD EXAMPLE:
Schema:
cdr_usage(sub_id, call_duration, sms_count, data_usage_mb)
Output:
```yaml
primary_domain: telecom usage
keywords:
  domain:
    - viễn thông
  activity:
    - hoạt động thuê bao
    - chi tiết cuộc gọi
  usage:
    - sử dụng dữ liệu
    - sử dụng SMS
    - sử dụng thoại
  aliases:
    - cdr
    - call detail record
```

BAD EXAMPLE (Avoid literal translations and technical fields):
Schema:
cdr_usage(sub_id, call_duration, sms_count, data_usage_mb, event_time)
Output:
```yaml
primary_domain: database table
keywords:
  domain:
    - thống kê
  activity:
    - sự kiện sử dụng
    - thời gian phát sinh
  usage:
    - sử dụng gọi thoại
    - số đếm tin nhắn
  aliases:
    - sub_id
    - event_time
```

### INPUT
Schema:
{schema_text}

Return ONLY valid YAML format inside a ```yaml ``` block. No other explanations."""


# Simple in-memory cache for keywords
_CACHE: dict[str, dict] = {}


def _get_schema_hash(schema_text: str) -> str:
    return hashlib.md5(schema_text.encode("utf-8")).hexdigest()


def _is_valid_keyword(kw: str) -> bool:
    kw = kw.strip().lower()
    if len(kw) < 2:
        return False
    if kw in GENERIC_BLACKLIST:
        return False
    # Technical regex: reject strings ending in _id, _at, _time or purely numeric/symbols
    if re.search(r'(_id|_at|_time|id)$', kw):
        return False
    if re.match(r'^[\W\d_]+$', kw):
        return False
    return True


def _semantic_deduplication(keywords: list[str]) -> list[str]:
    # Extremely basic semantic cluster/dedup.
    # In production, you might use vector similarity, but here we use simple normalizations.
    normalized = {}
    for kw in keywords:
        clean = kw.strip().lower()
        if not clean:
            continue
        # simple heuristic: use the shortest representative for highly overlapping phrases
        # e.g., "sử dụng dữ liệu" vs "lưu lượng dữ liệu"
        base = clean.replace("sử dụng ", "").replace("lưu lượng ", "")
        if base not in normalized or len(clean) < len(normalized[base]):
            normalized[base] = kw.strip()
    return list(normalized.values())


def _sync_generate_keywords(
    client: genai.Client,
    table_name: str,
    schema_text: str,
) -> dict:
    """Synchronous core that calls the LLM. Runs in a thread."""
    model = keyword_gen_model()

    abbrevs = "\n".join(f"- {k}: {v}" for k, v in ABBREVIATIONS.items())
    prompt = KEYWORD_PROMPT.format(abbreviations=abbrevs, schema_text=schema_text)

    conversation = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    config = types.GenerateContentConfig(temperature=0.0)

    response = generate_content(client, model, conversation, config)

    usage = extract_usage(response)
    debug_log(
        f"Token Usage ({table_name}) - Prompt: {usage.get('prompt_tokens', 0)} "
        f"| Completion: {usage.get('candidates_tokens', 0)} "
        f"| Total: {usage.get('total_tokens', 0)}"
    )

    raw = extract_text(response).strip()

    # Parse YAML block
    match = re.search(r'```(?:yaml)?\s*(.*?)\s*```', raw, re.DOTALL | re.IGNORECASE)
    yaml_str = match.group(1) if match else raw

    try:
        data = yaml.safe_load(yaml_str)
        final_data: dict = {}
        if isinstance(data, dict):
            if "primary_domain" in data:
                final_data["primary_domain"] = data["primary_domain"]

            final_data["keywords"] = {}
            flat_count = 0

            if "keywords" in data and isinstance(data["keywords"], dict):
                for cat, kws in data["keywords"].items():
                    if isinstance(kws, list):
                        valid = [kw for kw in kws if _is_valid_keyword(kw)]
                        deduped = _semantic_deduplication(valid)
                        if deduped:
                            final_data["keywords"][cat] = deduped
                            flat_count += len(deduped)

            if flat_count == 0:
                raise ValueError("No valid keywords found in categories.")
        else:
            raise ValueError("YAML root is not a dictionary.")

    except Exception as e:
        debug_log(f"Failed to parse YAML structurally for `{table_name}`: {e}")
        raw_keywords = []
        for line in yaml_str.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                kw = line[2:].strip(" '\",")
                if kw and _is_valid_keyword(kw):
                    raw_keywords.append(kw)
        raw_keywords = _semantic_deduplication(raw_keywords)[:8]
        final_data = {"keywords": {"fallback": raw_keywords}}
        flat_count = len(raw_keywords)

    debug_log(f"Generated {flat_count} keywords (categorized) for `{table_name}`")
    return final_data


async def generate_keywords_for_table(
    client: genai.Client,
    table_name: str,
    schema_text: str,
) -> dict:
    """Generate keywords for a single table (async-safe via thread pool)."""
    cache_key = _get_schema_hash(schema_text)
    if cache_key in _CACHE:
        debug_log(f"Cache hit for `{table_name}`")
        return _CACHE[cache_key]

    loop = asyncio.get_event_loop()
    final_data = await loop.run_in_executor(
        None, _sync_generate_keywords, client, table_name, schema_text
    )

    _CACHE[cache_key] = final_data
    return final_data


# Max concurrent LLM calls — tune to stay within API rate limits
_MAX_CONCURRENT = int(os.getenv("KEYWORD_GEN_CONCURRENCY", "10"))


async def generate_all_keywords(
    client: genai.Client,
    tables: dict[str, str],  # {table_name: compact_schema_text}
) -> dict[str, dict]:
    """Generate keywords for all tables with true parallelism.

    Uses a Semaphore to cap concurrent LLM calls at *_MAX_CONCURRENT*
    and ``run_in_executor`` inside each task so the blocking HTTP
    request doesn't stall the event loop.
    """
    result: dict[str, dict] = {}
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    total = len(tables)
    completed = 0
    t0 = time.perf_counter()

    async def process_table(table_name: str, schema_text: str):
        nonlocal completed
        async with semaphore:
            try:
                keywords = await generate_keywords_for_table(client, table_name, schema_text)
                return table_name, keywords
            except Exception as exc:
                debug_log(f"Keyword gen failed for `{table_name}`: {exc}")
                fallback_kws = table_name.replace("_", " ").split()
                return table_name, {"keywords": {"fallback": fallback_kws}}
            finally:
                completed += 1
                debug_log(f"Progress: {completed}/{total} tables done")

    tasks = [process_table(t_name, s_text) for t_name, s_text in tables.items()]
    results = await asyncio.gather(*tasks)

    for table_name, keywords in results:
        result[table_name] = keywords

    elapsed = time.perf_counter() - t0
    debug_log(f"All {total} tables done in {elapsed:.1f}s (concurrency={_MAX_CONCURRENT})")
    return result
