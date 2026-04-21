from __future__ import annotations

import os
from pathlib import Path

import psycopg


def build_dsn() -> str:
    return os.getenv(
        "DATABASE_URI",
        "postgresql://dev:dev123@localhost:5432/telco",
    )


def project_file(name: str) -> Path:
    return Path(__file__).with_name(name)


def read_sql_file(name: str) -> str:
    path = project_file(name)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {name}. Place the telco seed SQL at {path}."
        )
    return path.read_text(encoding="utf-8")


def main() -> None:
    sql = read_sql_file("telco_seed_init.sql")

    with psycopg.connect(build_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)

    print("Telco schema and sample data initialized.")


if __name__ == "__main__":
    main()
