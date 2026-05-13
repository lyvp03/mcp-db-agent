from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = BASE_DIR / "db_registry.json"
SCHEMA_CACHE_PATH = BASE_DIR / "schema_cache.json"


def default_source_id() -> str | None:
    value = os.getenv("DEFAULT_SOURCE_ID", "").strip()
    return value or None


def available_models() -> list[str]:
    value = os.getenv("AVAILABLE_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash,mimo-v2.5-pro").strip()
    models = [m.strip() for m in value.split(",") if m.strip()]
    return models if models else ["gemini-2.5-flash"]


def default_model() -> str:
    value = os.getenv("MODEL", "").strip()
    if value:
        return value
    models = available_models()
    return models[0] if models else "gemini-2.5-flash"


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL")


def embedding_dim() -> int:
    return int(os.getenv("EMBEDDING_DIM"))


def qdrant_url() -> str:
    return os.getenv("QDRANT_CLOUD_URL")


def qdrant_api_key() -> str | None:
    return os.getenv("QDRANT_API_KEY")


def qdrant_collection_prefix() -> str:
    return os.getenv("QDRANT_COLLECTION_PREFIX")


def keyword_gen_model() -> str:
    return os.getenv("KEYWORD_GEN_MODEL")


def schema_search_threshold() -> float:
    return float(os.getenv("SCHEMA_SEARCH_THRESHOLD", "0.63"))
