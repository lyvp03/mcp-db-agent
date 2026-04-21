from __future__ import annotations

import os
import time


def debug_enabled() -> bool:
    return os.getenv("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def debug_log(message: str) -> None:
    if not debug_enabled():
        return
    timestamp = time.strftime("%H:%M:%S")
    print(f"[AGENT DEBUG {timestamp}] {message}", flush=True)


def preview_text(value: str, limit: int = 500) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...(truncated)"
