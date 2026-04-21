from __future__ import annotations

from pathlib import Path


def load_system_prompt() -> str:
    prompt_path = Path(__file__).with_name("system_prompt.txt")
    return prompt_path.read_text(encoding="utf-8")
