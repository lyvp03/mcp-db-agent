# MCP DB Agent

A PostgreSQL agent demo built with `postgres-mcp` and Gemini, with support for:

- multiple database sources
- uploading `csv / xlsx / sql` files
- creating a new database from uploaded files
- introspecting schema once and caching it
- chatting with the agent against a selected source

## Architecture

```text
mcp-db-agent/
|-- agent.py               # CLI agent entrypoint
|-- init_db.py             # SQL bootstrap helper
|-- streamlit_app.py       # Streamlit entrypoint
|-- docker-compose.yml     # local PostgreSQL service
|-- system_prompt.txt      # system prompt and guardrails
|-- db_registry.json       # registered database sources
|-- schema_cache.json      # cached schema snapshots
|-- core/
|   |-- logging_utils.py   # debug logging helpers
|   |-- prompt.py          # prompt loader
|   |-- query_context.py   # build prompt context from cached schema
|   `-- settings.py        # project paths and env-backed settings
|-- adapters/
|   |-- gemini_adapter.py  # Gemini config, retries, tool schema conversion
|   |-- mcp_tools.py       # MCP tool call helpers
|   |-- server_config.py   # MCP stdio server parameters
|   `-- upload_importer.py # import csv/xlsx/sql into PostgreSQL
|-- services/
|   |-- registry.py        # read/write db_registry.json
|   |-- schema_service.py  # schema introspection and cache refresh
|   `-- schema_store.py    # read/write schema_cache.json
`-- ui/
    `-- streamlit_app.py   # actual Streamlit UI implementation
```

## Runtime Flow

1. Add a new source
   - upload files through Streamlit
   - create a new PostgreSQL database
   - import uploaded data
   - register the source in `db_registry.json`

2. Refresh schema
   - call MCP schema introspection tools
   - fetch the table list and table details
   - persist the snapshot into `schema_cache.json`

3. Query
   - choose a source
   - load schema cache
   - inject cached schema context into the prompt
   - if schema cache exists, the agent removes schema introspection tools from the model tool list
   - the model uses query/runtime tools such as `execute_sql`

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install postgres-mcp
```

## Docker

Start PostgreSQL locally:

```powershell
docker compose up -d
```

Stop PostgreSQL:

```powershell
docker compose down
```

Stop PostgreSQL and remove the volume:

```powershell
docker compose down -v
```

Check container status:

```powershell
docker compose ps
```

View PostgreSQL logs:

```powershell
docker compose logs -f
```

## Configuration

Create `.env` from `.env.example`:

```env
GEMINI_API_KEY=...
DATABASE_URI=postgresql://dev:dev123@localhost:5432/postgres
POSTGRES_MCP_COMMAND=postgres-mcp
POSTGRES_MCP_ARGS=--access-mode=restricted
MODEL=gemini-2.5-flash
MODEL_FALLBACKS=
DEBUG=1
DEFAULT_SOURCE_ID=
```

Notes:

- `DATABASE_URI` acts as the base/admin connection used when creating or importing demo sources.
- at query time, the selected source URI overrides the base URI
- `DEBUG=1` enables verbose execution logs in the terminal

## Running the Agent

If you already have at least one source in `db_registry.json`:

```powershell
python agent.py
```

## Running the Streamlit Demo

```powershell
streamlit run streamlit_app.py
```

The UI supports:

- uploading multiple files in a single action
- importing all uploaded files into the same new database
- creating and registering a new source
- refreshing schema cache per source
- chatting with the agent against the selected source

## Metadata Files

### `db_registry.json`

Stores registered sources:

```json
{
  "default_source_id": "sample_data",
  "sources": [
    {
      "source_id": "sample_data",
      "name": "Sample Data",
      "db_type": "postgres",
      "database_uri": "postgresql://dev:dev123@localhost:5432/sample_data",
      "schema_name": "public",
      "status": "active",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

### `schema_cache.json`

Stores schema snapshots by `source_id`. The agent uses this cache to build prompt context and avoid reloading schema on every query.

## Guardrails

- for billing / outstanding / debt style questions, the agent must use `invoices` and `payments`
- only rows with `payments.status = 'success'` count as paid money
- once schema cache exists, schema introspection tools are removed from the model tool list
- for arbitrary uploaded databases, the agent must use cached schema as the source of truth instead of assuming fixed table names
