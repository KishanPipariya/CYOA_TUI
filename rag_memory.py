"""
rag_memory.py — Semantic narrative memory backed by chromadb.

Stores past scene narratives as vector embeddings so that the most
semantically relevant past scenes can be retrieved and injected into the
LLM prompt as a "memory" block, giving it long-term story coherence
beyond the sliding context window.
"""
import uuid

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False


class NarrativeMemory:
    """
    In-memory chromadb collection for narrative scene embeddings.

    Gracefully degrades to a no-op if chromadb is unavailable.
    No persistence needed — the Neo4j graph DB stores everything permanently.
    """

    def __init__(self, collection_name: str = "scenes"):
        self._available = _CHROMA_AVAILABLE
        if not self._available:
            print("Warning: chromadb not installed. Narrative memory disabled.")
            return

        self._client = chromadb.Client()  # ephemeral in-memory client
        # Each session gets a unique collection so restarts start fresh
        unique_name = f"{collection_name}_{uuid.uuid4().hex[:8]}"
        self._collection = self._client.create_collection(
            name=unique_name,
            # Use chromadb's default embeddings (all-MiniLM-L6-v2)
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, scene_id: str, narrative: str) -> None:
        """Embed and store a scene narrative."""
        if not self._available:
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
        if not self._available:
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
