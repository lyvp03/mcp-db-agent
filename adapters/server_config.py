from __future__ import annotations

import os

from mcp import StdioServerParameters


def build_server_params() -> StdioServerParameters:
    database_uri = os.getenv("DATABASE_URI")
    if not database_uri:
        raise RuntimeError("DATABASE_URI is required.")
    return build_server_params_for_source(database_uri)


def build_server_params_for_source(database_uri: str) -> StdioServerParameters:
    command = os.getenv("POSTGRES_MCP_COMMAND", "uvx")
    raw_args = os.getenv("POSTGRES_MCP_ARGS", "postgres-mcp --access-mode=restricted")
    args = raw_args.split()

    env = os.environ.copy()
    env["DATABASE_URI"] = database_uri

    return StdioServerParameters(command=command, args=args, env=env)
