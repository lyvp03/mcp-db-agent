from __future__ import annotations

from google.genai import types


def schema_context_text(schema_snapshot: dict) -> str:
    notes: list[str] = []

    table_list_text = schema_snapshot.get("table_list_text", "").strip()
    if table_list_text:
        notes.append(
            f"Tables discovered in schema `{schema_snapshot.get('schema_name', 'public')}`:\n"
            f"{table_list_text}"
        )

    for table_name, table_schema in schema_snapshot.get("tables", {}).items():
        notes.append(
            f"Schema for `{schema_snapshot.get('schema_name', 'public')}.{table_name}`:\n"
            f"{table_schema}"
        )

    return "\n\n".join(notes).strip()


def schema_context_message(schema_snapshot: dict) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Grounded database schema context loaded from schema cache. "
                    "Use this as the primary schema reference before answering.\n\n"
                    f"{schema_context_text(schema_snapshot)}"
                )
            )
        ],
    )
