from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.settings import SCHEMA_CACHE_PATH
from core.logging_utils import debug_log


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_payload() -> dict[str, Any]:
    if not SCHEMA_CACHE_PATH.exists():
        return {"schemas": {}}
    return json.loads(SCHEMA_CACHE_PATH.read_text(encoding="utf-8"))


def _save_payload(payload: dict[str, Any]) -> None:
    debug_log(f"Writing schema cache to: {SCHEMA_CACHE_PATH}")
    SCHEMA_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_schema_snapshot(source_id: str) -> dict[str, Any] | None:
    return _load_payload().get("schemas", {}).get(source_id)


def save_schema_snapshot(source_id: str, schema_name: str, table_list_text: str, tables: dict[str, str]) -> None:
    payload = _load_payload()
    schemas = payload.setdefault("schemas", {})
    debug_log(
        f"Saving schema snapshot for `{source_id}` with "
        f"{len(tables)} tables"
    )
    schemas[source_id] = {
        "source_id": source_id,
        "schema_name": schema_name,
        "captured_at": utc_now_iso(),
        "table_list_text": table_list_text,
        "tables": tables,
    }
    _save_payload(payload)
    debug_log(f"Schema snapshot saved for `{source_id}`")
