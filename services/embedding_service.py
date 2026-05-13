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
    """Build the text to embed for a table by flattening categorized keywords."""
    parts = []
    
    # Add primary domain if it exists
    if "primary_domain" in keyword_data:
        parts.append(str(keyword_data["primary_domain"]))
        
    # Flatten the categories
    kws = keyword_data.get("keywords", {})
    flat_list = []
    if isinstance(kws, dict):
        for cat, values in kws.items():
            if isinstance(values, list):
                flat_list.extend([str(v) for v in values])
    elif isinstance(kws, list):
        flat_list.extend([str(v) for v in kws])
        
    if flat_list:
        parts.append(", ".join(flat_list))
        
    return f"{table_name}: {' - '.join(parts)}"
