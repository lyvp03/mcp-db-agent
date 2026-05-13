from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SECTION_PATTERN = re.compile(
    r"^\[(?P<name>[a-zA-Z0-9_]+)\]\s*\n(?P<body>.*?)(?=^\[[a-zA-Z0-9_]+\]\s*\n|\Z)",
    re.MULTILINE | re.DOTALL,
)


def load_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "system_prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


def load_prompt_sections() -> dict[str, str]:
    text = load_system_prompt()
    sections: dict[str, str] = {}
    for match in SECTION_PATTERN.finditer(text):
        sections[match.group("name").strip().lower()] = match.group("body").strip()
    return sections


def selected_guardrail_sections(
    question: str,
    schema_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    """Return all available guardrail sections.
    Since the system prompt is now highly compact, we inject all rules by default
    instead of using fragile keyword matching.
    """
    sections = load_prompt_sections()
    return list(sections.keys())


def build_system_prompt(
    question: str,
    schema_snapshot: dict[str, Any] | None = None,
) -> str:
    sections = load_prompt_sections()
    ordered_names = selected_guardrail_sections(question, schema_snapshot)
    parts = [sections[name] for name in ordered_names if name in sections]
    return "\n\n".join(part for part in parts if part.strip())


def describe_selected_guardrails(
    question: str,
    schema_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    return selected_guardrail_sections(question, schema_snapshot)
