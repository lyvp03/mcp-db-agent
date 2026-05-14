"""Embedding service using Gemini Embedding API."""
import os
import asyncio
from google import genai
from google.genai import types
from core.settings import embedding_model, embedding_dim
from core.logging_utils import debug_log


def get_embed_client() -> genai.Client:
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def embed_text(client: genai.Client, text: str) -> list[float]:
    """Embed a single text string, return vector."""
    dim = embedding_dim()
    result = client.models.embed_content(
        model=embedding_model(),
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=dim)
    )
    return result.embeddings[0].values


def embed_texts(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in batch sequentially."""
    vectors = []
    for text in texts:
        vectors.append(embed_text(client, text))
    return vectors


async def embed_texts_async(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """Embed multiple texts concurrently using asyncio.gather."""
    # Run synchronous embed_text in thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, embed_text, client, text)
        for text in texts
    ]
    vectors = await asyncio.gather(*tasks)
    return list(vectors)


def build_embed_text(table_name: str, keyword_data: dict) -> str:
    """Build the text to embed for a table by flattening categorized keywords.

    Legacy function — kept for backward compatibility.
    New code should use build_dense_text() or build_sparse_text().
    """
    return build_dense_text(table_name, keyword_data)


def build_dense_text(table_name: str, kw_data: dict) -> str:
    """Concise text for dense embedding (semantic matching).

    Dense embeddings work best with focused, concise text that
    captures the semantic essence of the table.
    """
    parts = []
    if "primary_domain" in kw_data:
        parts.append(str(kw_data["primary_domain"]))
    kws = kw_data.get("keywords", {})
    flat = [str(v) for cat in kws.values() if isinstance(cat, list) for v in cat]
    if flat:
        parts.append(", ".join(flat))
    # Add 2-3 most relevant hypothetical questions
    questions = kw_data.get("hypothetical_questions", [])[:3]
    if questions:
        parts.append(" | ".join(questions))
    return f"{table_name}: {' — '.join(parts)}"


def build_sparse_text(table_name: str, kw_data: dict) -> str:
    """Focused text for BM25 sparse matching (Technical/Exact keywords only).
    
    BM25 acts as a safety net for exact table names and English abbreviations.
    Semantic Vietnamese questions and common domains are left to Dense Embeddings.
    """
    parts = [table_name.replace("_", " ")]
    
    # Only include aliases (which typically contain technical abbreviations like CDR, KYC)
    kws = kw_data.get("keywords", {})
    if isinstance(kws, dict) and "aliases" in kws:
        aliases = kws["aliases"]
        if isinstance(aliases, list):
            parts.extend([str(v) for v in aliases])
            
    return " ".join(parts)
