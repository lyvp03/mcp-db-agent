from __future__ import annotations

import asyncio
import os

import streamlit as st
from dotenv import load_dotenv

from adapters.upload_importer import admin_connection_uri, create_database, database_uri_for_name, import_uploaded_file, slugify
from agent import ask_database
from core.settings import available_models, default_model
from core.token_tracker import AgentResult
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
        st.session_state.last_tool_estimates = []
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

    if "last_tool_estimates" in st.session_state and st.session_state.last_tool_estimates:
        st.divider()
        st.header("⚙️ Tool Token Budget")
        estimates = st.session_state.last_tool_estimates
        total_tools = len(estimates)
        active_tools = len([t for t in estimates if t.is_active])
        total_tokens = sum(t.estimated_tokens for t in estimates)
        active_tokens = sum(t.estimated_tokens for t in estimates if t.is_active)
        saved_tokens = total_tokens - active_tokens
        
        st.write(f"**Total:** {total_tools} tools (~{total_tokens:,} tokens)")
        st.write(f"**Active:** {active_tools} tools (~{active_tokens:,} tokens)")
        st.write(f"**Saved:** ~{saved_tokens:,} tokens")
        
        with st.expander("Per-tool breakdown"):
            for t in estimates:
                status = "✅" if t.is_active else "⛔"
                st.write(f"{status} `{t.tool_name}`: ~{t.estimated_tokens} tokens")


col1, col2 = st.columns(2)
with col1:
    source_map = source_options()
    selected_name = st.selectbox("Choose database source", options=list(source_map.keys()), index=0 if source_map else None)
    selected_source_id = source_map.get(selected_name) if selected_name else None

with col2:
    models = available_models()
    default_m = default_model()
    try:
        def_idx = models.index(default_m)
    except ValueError:
        models.insert(0, default_m)
        def_idx = 0
    selected_model = st.selectbox("Choose AI Model", options=models, index=def_idx)


def render_assistant_result(result: AgentResult | str):
    if isinstance(result, str):
        st.markdown(result)
        return
        
    st.markdown(result.answer)
    
    if result.total_tokens > 0:
        with st.expander(f"📊 Token Usage ({result.total_tokens:,} tokens)"):
            st.markdown(f"**Model:** `{result.model_used}`")
            cols = st.columns(4)
            cols[0].metric("📥 Input", f"{result.total_prompt_tokens:,}")
            cols[1].metric("📤 Output", f"{result.total_candidates_tokens:,}")
            cols[2].metric("🧠 Thinking", f"{result.total_thoughts_tokens:,}")
            cols[3].metric("📊 Total", f"{result.total_tokens:,}")

            schema_context_tokens = getattr(result, "schema_context_tokens", 0)
            system_prompt_tokens = getattr(result, "system_prompt_tokens", 0)
            if schema_context_tokens > 0 or system_prompt_tokens > 0:
                st.markdown("**Input Breakdown (Estimated):**")
                cols_est = st.columns(2)
                cols_est[0].metric("System Prompt", f"~{system_prompt_tokens:,}")
                cols_est[1].metric("Schema Context", f"~{schema_context_tokens:,}")
            
            for t in result.turns:
                st.markdown(f"**Turn {t.turn_number}:** prompt={t.prompt_tokens:,} | output={t.candidates_tokens:,} | think={t.thoughts_tokens:,}")
                if t.tool_calls:
                    st.markdown(f"🔧 Tools: {', '.join(t.tool_calls)}")

    if result.sql_statements:
        with st.expander(f"🗄️ SQL Queries ({len(result.sql_statements)})"):
            for sql in result.sql_statements:
                st.code(sql, language="sql")
                
    has_thinking = any(t.thinking_text.strip() for t in result.turns)
    if has_thinking:
        with st.expander("🧠 AI Thinking Process"):
            for t in result.turns:
                if t.thinking_text.strip():
                    st.markdown(f"**Turn {t.turn_number}:**")
                    st.markdown(t.thinking_text)

    if result.route_type:
        route_icons = {"direct": "🎯", "graph": "🔗", "fallback": "🔄", "keyword": "🔑"}
        route_icon = route_icons.get(result.route_type, "❓")
        with st.expander(f"{route_icon} Schema Routing: {result.route_type.upper()}"):
            st.markdown(f"**Strategy:** `{result.route_type}`")
            if result.selected_tables:
                st.markdown(f"**Selected Tables ({len(result.selected_tables)}):**")
                for t in result.selected_tables:
                    st.markdown(f"- `{t}`")
            if result.join_paths:
                st.markdown("---")
                st.markdown(f"**🗺️ BFS Join Paths ({len(result.join_paths)}):**")
                for i, path in enumerate(result.join_paths, 1):
                    if len(path) >= 2:
                        root = path[0]
                        end = path[-1]
                        path_str = " → ".join(f"`{t}`" for t in path)
                        st.markdown(f"**{i}. Root:** `{root}` 🎯 **End:** `{end}`")
                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;🔗 **Path:** {path_str}", unsafe_allow_html=True)
                    else:
                        st.markdown(f"**{i}. Single Table:** `{path[0]}`")
            if result.search_scores:
                st.markdown("---")
                st.markdown("**Semantic Search Scores (Top-K):**")
                for table_name, score in result.search_scores:
                    bar_pct = min(score * 100, 100)
                    confidence = "🟢" if score >= 0.65 else "🟡" if score >= 0.4 else "🔴"
                    st.markdown(f"{confidence} `{table_name}`: **{score:.4f}**")
                    st.progress(bar_pct / 100)

    schema_context_text = getattr(result, "schema_context_text", "")
    if schema_context_text:
        with st.expander("📝 Injected Schema Context"):
            st.markdown("This is the exact text payload injected into the prompt's context window:")
            st.code(schema_context_text, language="sql")


if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
    
for item in st.session_state.chat_history:
    with st.chat_message(item["role"]):
        if item["role"] == "user":
            st.markdown(item["content"])
        else:
            render_assistant_result(item["content"])

question = st.chat_input("Ask about the selected database")
if question:
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
        
    with st.chat_message("assistant"):
        try:
            with st.spinner(f"Thinking with {selected_model}..."):
                answer = ask_database(question, source_id=selected_source_id, model_name=selected_model)
            
            render_assistant_result(answer)
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            
            if hasattr(answer, "tool_estimates") and answer.tool_estimates:
                st.session_state.last_tool_estimates = answer.tool_estimates
                st.rerun()
                
        except Exception as exc:
            st.exception(exc)
