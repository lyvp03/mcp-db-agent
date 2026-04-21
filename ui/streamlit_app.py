from __future__ import annotations

import asyncio
import os

import streamlit as st
from dotenv import load_dotenv

from adapters.upload_importer import admin_connection_uri, create_database, database_uri_for_name, import_uploaded_file, slugify
from agent import ask_database
from services.registry import DatabaseSource, get_default_source, list_sources, upsert_source
from services.schema_service import refresh_schema_cache

load_dotenv()
st.set_page_config(page_title="Multi-DB MCP", layout="wide")
st.title("Multi-DB MCP")


def base_database_uri() -> str:
    database_uri = os.getenv("DATABASE_URI", "").strip()
    if not database_uri:
        raise RuntimeError("DATABASE_URI is required in .env")
    return database_uri


def source_options() -> dict[str, str]:
    return {source.name: source.source_id for source in list_sources()}
with st.sidebar:
    if st.button("New chat", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()
    st.header("Upload Database")
    uploaded_files = st.file_uploader("Upload csv / xlsx / sql", type=["csv", "xlsx", "xls", "sql"], accept_multiple_files=True)
    source_name = st.text_input("Source name", value="Demo DB")
    db_name = st.text_input("Database name", value="demo_db")
    make_default = st.checkbox("Set as default source", value=True)

    if st.button("Create / Import", use_container_width=True):
        if not uploaded_files:
            st.error("Choose at least one file first.")
        else:
            try:
                db_slug = slugify(db_name)
                source_slug = slugify(source_name)
                target_uri = database_uri_for_name(base_database_uri(), db_slug)
                create_database(admin_connection_uri(base_database_uri()), db_slug)
                imported_names: list[str] = []
                for uploaded_file in uploaded_files:
                    import_uploaded_file(target_uri, uploaded_file.name, uploaded_file.getvalue())
                    imported_names.append(uploaded_file.name)
                source = DatabaseSource(source_id=source_slug, name=source_name, db_type="postgres", database_uri=target_uri, schema_name="public")
                upsert_source(source, make_default=make_default)
                asyncio.run(refresh_schema_cache(source))
                st.success(f"Imported files into database `{db_slug}`: {', '.join(f'`{name}`' for name in imported_names)}.")
            except Exception as exc:
                st.exception(exc)

    st.divider()
    st.header("Sources")
    
    available_sources = list_sources()
    if not available_sources:
        st.info("No registered sources yet.")
    else:
        default_source = get_default_source()
        for source in available_sources:
            label = f"{source.name} ({source.source_id})"
            if default_source is not None and source.source_id == default_source.source_id:
                label += " [default]"
            st.write(label)
            if st.button(f"Refresh schema: {source.source_id}", key=f"refresh_{source.source_id}"):
                try:
                    asyncio.run(refresh_schema_cache(source))
                    st.success(f"Refreshed schema cache for `{source.source_id}`.")
                except Exception as exc:
                    st.exception(exc)

source_map = source_options()
selected_name = st.selectbox("Choose database source", options=list(source_map.keys()), index=0 if source_map else None)
selected_source_id = source_map.get(selected_name) if selected_name else None

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
for item in st.session_state.chat_history:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])

question = st.chat_input("Ask about the selected database")
if question:
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            answer = ask_database(question, source_id=selected_source_id)
            st.markdown(answer)
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
        except Exception as exc:
            st.exception(exc)
