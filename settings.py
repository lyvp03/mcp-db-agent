from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = BASE_DIR / "db_registry.json"
SCHEMA_CACHE_PATH = BASE_DIR / "schema_cache.json"


def default_source_id() -> str | None:
    value = os.getenv("DEFAULT_SOURCE_ID", "").strip()
    return value or None
