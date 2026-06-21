"""
cortex/redis_store.py
---------------------
Embed context notes and store in Redis Stack for semantic vector search.
Uses OpenAI text-embedding-3-small (cheap, fast) or Claude Embeddings.

Start Redis Stack: docker run -p 6379:6379 redis/redis-stack
"""

import json
import os
from datetime import datetime
from typing import Optional

import redis
from redis.commands.search.field import TextField, VectorField, TagField, NumericField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

# Use Voyage AI embeddings (Anthropic's embedding model, 1024-dim)
import voyageai

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
INDEX_NAME = "cortex_idx"
VECTOR_DIM = 1024  # voyage-3 dimension
DOC_PREFIX = "cortex:note:"

_client = None
_voyage_client = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=False)
    return _client


def _embed(text: str) -> list[float]:
    """Get embedding vector for text using Voyage AI (Anthropic)."""
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)

    result = _voyage_client.embed(
        [text[:32000]],  # voyage-3 supports up to 32k tokens
        model="voyage-3"
    )
    return result.embeddings[0]


def setup_index():
    """Create the Redis vector search index. Run once."""
    r = _redis()
    try:
        r.ft(INDEX_NAME).info()
        print(f"Index '{INDEX_NAME}' already exists.")
        return
    except Exception:
        pass  # Index doesn't exist yet

    schema = (
        TextField("content"),
        TextField("source"),
        TextField("type"),
        TagField("tags"),
        NumericField("timestamp_unix"),
        VectorField(
            "embedding",
            "FLAT",
            {
                "TYPE": "FLOAT32",
                "DIM": VECTOR_DIM,
                "DISTANCE_METRIC": "COSINE",
            }
        )
    )

    r.ft(INDEX_NAME).create_index(
        schema,
        definition=IndexDefinition(prefix=[DOC_PREFIX], index_type=IndexType.HASH)
    )
    print(f"✅ Created Redis index '{INDEX_NAME}'")


def embed_and_store(extracted: dict, raw_text: str = "") -> list[str]:
    """
    Embed all extracted context items and store in Redis.
    Returns list of Redis keys that were stored.
    """
    r = _redis()
    source = extracted.get("_source", "unknown")
    timestamp = extracted.get("_timestamp", datetime.now().isoformat())
    timestamp_unix = int(datetime.fromisoformat(timestamp).timestamp())

    stored_keys = []

    def _store_item(content: str, note_type: str, extra_tags: list[str] = None):
        if not content.strip():
            return
        try:
            embedding = _embed(content)
            import struct
            embedding_bytes = struct.pack(f"{len(embedding)}f", *embedding)

            key = f"{DOC_PREFIX}{note_type}:{source}:{timestamp_unix}:{len(stored_keys)}"
            mapping = {
                b"content": content.encode("utf-8"),
                b"source": source.encode("utf-8"),
                b"type": note_type.encode("utf-8"),
                b"tags": (",".join([note_type, source] + (extra_tags or []))).encode("utf-8"),
                b"timestamp_unix": str(timestamp_unix).encode("utf-8"),
                b"embedding": embedding_bytes,
            }
            r.hset(key, mapping=mapping)
            stored_keys.append(key)
        except Exception as e:
            print(f"  ⚠️ Failed to embed {note_type}: {e}", flush=True)

    for insight in extracted.get("KEY_INSIGHTS", []):
        _store_item(insight, "insight")

    for decision in extracted.get("DECISIONS", []):
        _store_item(decision, "decision")

    for question in extracted.get("OPEN_QUESTIONS", []):
        _store_item(question, "open-question")

    for action in extracted.get("ACTION_ITEMS", []):
        _store_item(action, "action")

    for person in extracted.get("PEOPLE", []):
        if isinstance(person, dict):
            content = f"{person.get('name', '')}: {person.get('context', '')}"
        else:
            content = str(person)
        _store_item(content, "person")

    if extracted.get("SUMMARY"):
        _store_item(extracted["SUMMARY"], "summary")

    return stored_keys


def search_context(query: str, top_k: int = 5, note_type: str = None) -> list[dict]:
    """
    Semantic search over stored context.

    Args:
        query: Natural language query
        top_k: Number of results
        note_type: Optional filter by type (insight, decision, open-question, etc.)

    Returns:
        List of dicts with keys: content, source, type, timestamp_unix, score
    """
    r = _redis()
    embedding = _embed(query)

    import struct
    embedding_bytes = struct.pack(f"{len(embedding)}f", *embedding)

    # Build query
    if note_type:
        q = (
            Query(f"@type:{{{note_type}}}=>[KNN {top_k} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("content", "source", "type", "timestamp_unix", "score")
            .dialect(2)
        )
    else:
        q = (
            Query(f"*=>[KNN {top_k} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("content", "source", "type", "timestamp_unix", "score")
            .dialect(2)
        )

    results = r.ft(INDEX_NAME).search(q, query_params={"vec": embedding_bytes})

    output = []
    for doc in results.docs:
        ts_unix = int(getattr(doc, "timestamp_unix", 0) or 0)
        output.append({
            "content": getattr(doc, "content", ""),
            "source": getattr(doc, "source", ""),
            "type": getattr(doc, "type", ""),
            "timestamp": datetime.fromtimestamp(ts_unix).isoformat() if ts_unix else "",
            "score": float(getattr(doc, "score", 1.0)),
        })

    return output


def get_recent_context(since: datetime, top_k: int = 20) -> list[dict]:
    """Get context items captured after a given datetime."""
    r = _redis()
    since_unix = int(since.timestamp())

    q = (
        Query(f"@timestamp_unix:[{since_unix} +inf]")
        .sort_by("timestamp_unix", asc=False)
        .return_fields("content", "source", "type", "timestamp_unix")
        .paging(0, top_k)
    )

    results = r.ft(INDEX_NAME).search(q)
    output = []
    for doc in results.docs:
        ts_unix = int(getattr(doc, "timestamp_unix", 0) or 0)
        output.append({
            "content": getattr(doc, "content", ""),
            "source": getattr(doc, "source", ""),
            "type": getattr(doc, "type", ""),
            "timestamp": datetime.fromtimestamp(ts_unix).isoformat() if ts_unix else "",
        })

    return output


if __name__ == "__main__":
    print("Setting up Cortex Redis index...")
    setup_index()
    print("Done.")
