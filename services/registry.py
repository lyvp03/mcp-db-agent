from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from core.settings import REGISTRY_PATH, default_source_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DatabaseSource:
    source_id: str
    name: str
    db_type: str
    database_uri: str
    schema_name: str = "public"
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatabaseSource":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_payload() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"default_source_id": default_source_id(), "sources": []}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _save_payload(payload: dict[str, Any]) -> None:
    REGISTRY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_sources() -> list[DatabaseSource]:
    return [DatabaseSource.from_dict(item) for item in _load_payload().get("sources", [])]


def get_source(source_id: str) -> DatabaseSource | None:
    for source in list_sources():
        if source.source_id == source_id:
            return source
    return None


def get_default_source() -> DatabaseSource | None:
    payload = _load_payload()
    selected_id = payload.get("default_source_id") or default_source_id()
    if not selected_id:
        sources = list_sources()
        return sources[0] if sources else None
    return get_source(selected_id)


def upsert_source(source: DatabaseSource, make_default: bool = False) -> None:
    payload = _load_payload()
    sources = payload.get("sources", [])
    updated_sources: list[dict[str, Any]] = []
    found = False
    if not source.created_at:
        source.created_at = utc_now_iso()
    source.updated_at = utc_now_iso()
    for item in sources:
        if item["source_id"] == source.source_id:
            updated_sources.append(source.to_dict())
            found = True
        else:
            updated_sources.append(item)
    if not found:
        updated_sources.append(source.to_dict())
    payload["sources"] = updated_sources
    if make_default or not payload.get("default_source_id"):
        payload["default_source_id"] = source.source_id
    _save_payload(payload)
