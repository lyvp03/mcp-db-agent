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


def _question_lower(question: str) -> str:
    return question.casefold()


def _has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _schema_text(schema_snapshot: dict[str, Any] | None) -> str:
    if not schema_snapshot:
        return ""
    tables = schema_snapshot.get("tables", {})
    table_list_text = schema_snapshot.get("table_list_text", "")
    parts = [table_list_text] if isinstance(table_list_text, str) else []
    if isinstance(tables, dict):
        parts.extend(str(name) for name in tables.keys())
        parts.extend(str(value) for value in tables.values())
    return "\n".join(parts).casefold()


def selected_guardrail_sections(
    question: str,
    schema_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    q = _question_lower(question)
    schema = _schema_text(schema_snapshot)
    selected = ["base", "uploaded_db", "numeric"]

    if _has_any(
        q,
        [
            "cong no",
            "công nợ",
            "outstanding",
            "debt",
            "overdue",
            "receivable",
            "billing",
            "invoice",
            "payment",
        ],
    ):
        selected.append("billing")

    if _has_any(
        q,
        [
            "data usage",
            "amt_data",
            "usage",
            "subscriber",
            "thuê bao",
            "thue bao",
            "isdn",
            "msisdn",
            "plan",
            "goi cuoc",
            "gói cước",
        ],
    ) or _has_any(schema, ["subscriptions", "usage_records", "msisdn", "isdn", "amt_data"]):
        selected.append("telco")

    if _has_any(
        q,
        [
            "nghệ an",
            "nghe an",
            "hà nội",
            "ha noi",
            "đà nẵng",
            "da nang",
            "city",
            "province",
            "tỉnh",
            "thành phố",
            "địa chỉ",
            "dia chi",
            "address",
        ],
    ):
        selected.append("location")

    if _has_any(
        q,
        [
            "top",
            "cao nhat",
            "lớn nhất",
            "lon nhat",
            "nhiều nhất",
            "nhieu nhat",
            "vừa",
            "vua",
            "highest",
            "largest",
            "most",
        ],
    ):
        selected.append("ranking")

    deduped: list[str] = []
    for name in selected:
        if name not in deduped:
            deduped.append(name)
    return deduped


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
