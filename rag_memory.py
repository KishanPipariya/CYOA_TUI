"""
rag_memory.py — Semantic narrative memory backed by chromadb.

Stores past scene narratives as vector embeddings so that the most
semantically relevant past scenes can be retrieved and injected into the
LLM prompt as a "memory" block, giving it long-term story coherence
beyond the sliding context window.
"""
import uuid
from typing import Any, Optional

__all__ = ["NarrativeMemory"]

try:
    import chromadb  # type: ignore
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False


class NarrativeMemory:
    """
    In-memory chromadb collection for narrative scene embeddings.

    Gracefully degrades to a no-op if chromadb is unavailable.
    No persistence needed — the Neo4j graph DB stores everything permanently.

    Fix #7: Lazy-initialises the chromadb client and embedding model on the
    first add() call, not at __init__ time. This avoids blocking the main
    thread at app startup while chromadb downloads/loads all-MiniLM-L6-v2.
    """

    def __init__(self, collection_name: str = "scenes") -> None:
        self._available: bool = _CHROMA_AVAILABLE
        self._collection_name: str = collection_name
        # Fix #7: defer client + collection creation to first use
        self._client: Optional[Any] = None
        self._collection: Optional[Any] = None

    def _ensure_ready(self) -> bool:
        """Create the chroma client and collection on first use. Returns False if unavailable."""
        if not self._available:
            return False
        if self._collection is not None:
            return True
        try:
            self._client = chromadb.Client()
            unique_name = f"{self._collection_name}_{uuid.uuid4().hex[:8]}"
            self._collection = self._client.create_collection(
                name=unique_name,
                metadata={"hnsw:space": "cosine"},
            )
            return True
        except Exception as e:
            print(f"RAG memory: failed to initialise chromadb: {e}")
            self._available = False
            return False

    def add(self, scene_id: str, narrative: str) -> None:
        """Embed and store a scene narrative."""
        if not self._ensure_ready() or self._collection is None:
            return
        try:
            self._collection.upsert(
                ids=[scene_id],
                documents=[narrative],
            )
        except Exception as e:
            print(f"RAG memory: failed to add scene {scene_id}: {e}")

    def query(self, text: str, n: int = 3) -> list[str]:
        """
        Return the top-N most semantically relevant past narratives.
        Returns an empty list if memory is unavailable or empty.
        """
        if not self._ensure_ready() or self._collection is None:
            return []
        try:
            count = self._collection.count()
            if count == 0:
                return []
            n = min(n, count)
            results = self._collection.query(
                query_texts=[text],
                n_results=n,
            )
            return results["documents"][0] if results["documents"] else []
        except Exception as e:
            print(f"RAG memory: query failed: {e}")
            return []
