"""Qdrant vector store for table embeddings — hybrid (dense + sparse)."""
import hashlib
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, SparseIndexParams,
    PointStruct, Filter, FieldCondition, MatchValue,
    SparseVector, Modifier, Prefetch, FusionQuery, Fusion,
)
from core.settings import qdrant_url, qdrant_api_key, qdrant_collection_prefix, embedding_dim
from core.logging_utils import debug_log


def get_qdrant_client() -> QdrantClient:
    api_key = qdrant_api_key()
    if api_key:
        return QdrantClient(url=qdrant_url(), api_key=api_key)
    return QdrantClient(url=qdrant_url())


def collection_name(source_id: str) -> str:
    """Each source gets its own collection."""
    prefix = qdrant_collection_prefix()
    safe_id = source_id.replace("-", "_").replace(" ", "_")
    return f"{prefix}_{safe_id}"


def ensure_collection(client: QdrantClient, source_id: str) -> str:
    """Create collection with named vectors (dense + sparse) if not exists.

    Returns the collection name.
    """
    name = collection_name(source_id)
    collections = [c.name for c in client.get_collections().collections]
    if name not in collections:
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(
                    size=embedding_dim(),
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                    modifier=Modifier.IDF,  # Qdrant computes IDF weights
                ),
            },
        )
        debug_log(f"Created Qdrant collection (hybrid): {name}")
    return name


def upsert_table_vectors(
    client: QdrantClient,
    source_id: str,
    dense_vectors: dict[str, list[float]],       # {table_name: dense_vector}
    sparse_vectors: dict[str, tuple[list[int], list[float]]],  # {table_name: (indices, values)}
    table_keywords: dict[str, dict],              # {table_name: keyword_data}
) -> None:
    """Upsert all table vectors (dense + sparse) into Qdrant."""
    coll = ensure_collection(client, source_id)
    points = []
    for table_name, dense_vec in dense_vectors.items():
        sparse_idx, sparse_val = sparse_vectors.get(table_name, ([], []))
        # Hash table_name to generate a stable integer ID for Qdrant
        hash_id = int(hashlib.md5(table_name.encode('utf-8')).hexdigest()[:15], 16)
        kw_data = table_keywords.get(table_name, {})

        points.append(PointStruct(
            id=hash_id,
            vector={
                "dense": dense_vec,
                "sparse": SparseVector(indices=sparse_idx, values=sparse_val),
            },
            payload={
                "table_name": table_name,
                "table_role": kw_data.get("table_role", "unknown"),
                "source_id": source_id,
                "keyword_data": kw_data,
            },
        ))
    if points:
        client.upsert(collection_name=coll, points=points)
        debug_log(f"Upserted {len(points)} vectors (hybrid) to Qdrant collection `{coll}`")


def delete_collection(client: QdrantClient, source_id: str) -> None:
    """Delete a collection if it exists (for re-indexing)."""
    name = collection_name(source_id)
    try:
        collections = [c.name for c in client.get_collections().collections]
        if name in collections:
            client.delete_collection(collection_name=name)
            debug_log(f"Deleted Qdrant collection: {name}")
    except Exception as e:
        debug_log(f"Failed to delete collection `{name}`: {e}")


def search_tables(
    client: QdrantClient,
    source_id: str,
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Dense-only search (backward compatible).

    Falls back to named vector search if collection uses named vectors.
    """
    coll = collection_name(source_id)
    try:
        results = client.query_points(
            collection_name=coll,
            query=query_vector,
            using="dense",
            limit=top_k,
            with_payload=True,
        )
        return [{"score": r.score, "payload": r.payload} for r in results.points]
    except Exception as e:
        # Fallback: try unnamed vector (old collections)
        debug_log(f"Named vector search failed, trying unnamed: {e}")
        try:
            results = client.query_points(
                collection_name=coll,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            )
            return [{"score": r.score, "payload": r.payload} for r in results.points]
        except Exception as e2:
            debug_log(f"Qdrant search failed: {e2}")
            return []


def hybrid_search(
    client: QdrantClient,
    source_id: str,
    dense_vector: list[float],
    sparse_indices: list[int],
    sparse_values: list[float],
    top_k: int = 10,
) -> list[dict]:
    """Hybrid search: dense + sparse → RRF fusion server-side.

    Uses Qdrant's native prefetch + fusion API for zero-code RRF.
    """
    coll = collection_name(source_id)
    try:
        results = client.query_points(
            collection_name=coll,
            prefetch=[
                Prefetch(
                    query=SparseVector(indices=sparse_indices, values=sparse_values),
                    using="sparse",
                    limit=top_k,
                ),
                Prefetch(
                    query=dense_vector,
                    using="dense",
                    limit=top_k,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        return [{"score": r.score, "payload": r.payload} for r in results.points]
    except Exception as e:
        debug_log(f"Hybrid search failed, falling back to dense-only: {e}")
        return search_tables(client, source_id, dense_vector, top_k=top_k)
