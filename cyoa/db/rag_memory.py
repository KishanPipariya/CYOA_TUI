"""
rag_memory.py — Semantic narrative memory backed by chromadb.

Stores past scene narratives as vector embeddings so that the most
semantically relevant past scenes can be retrieved and injected into the
LLM prompt as a "memory" block, giving it long-term story coherence
beyond the sliding context window.
"""

import uuid
import logging
from typing import Any, Optional

__all__ = ["NarrativeMemory", "NPCMemory"]

try:
    import chromadb  # type: ignore
    from cyoa.core.events import bus

    _CHROMA_AVAILABLE = True
    logger = logging.getLogger(__name__)
except ImportError:
    _CHROMA_AVAILABLE = False
    logger = logging.getLogger(
        __name__
    )  # Still need logger even if chromadb is not available


class NarrativeMemory:
    """
    In-memory chromadb collection for narrative scene embeddings.

    Gracefully degrades to a no-op if chromadb is unavailable.
    No persistence needed — the Neo4j graph DB stores everything permanently.

    Fix #7: Lazy-initialises the chromadb client and embedding model on the
    first add() call, not at __init__ time. This avoids blocking the main
    thread at app startup while chromadb downloads/loads all-MiniLM-L6-v2.
    """

    def __init__(self, collection_name: str = "cyoa_narrative_memory") -> None:
        self._available: bool = _CHROMA_AVAILABLE
        self._collection_name: str = collection_name
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
        except Exception as e:  # noqa: BLE001
            logger.error("RAG memory: failed to initialise chromadb: %s", e)
            self._available = False
            return False

    async def add_async(self, scene_id: str, narrative: str) -> None:
        """Embed and store a scene narrative asynchronously."""
        import asyncio
        def _sync_add():
            if not self._ensure_ready() or self._collection is None:
                return
            try:
                self._collection.upsert(
                    ids=[scene_id],
                    documents=[narrative],
                )
            except Exception as e:  # noqa: BLE001
                logger.error("RAG memory: failed to add scene %s: %s", scene_id, e)
        await asyncio.to_thread(_sync_add)

    async def query_async(self, text: str, n: int = 3) -> list[str]:
        """
        Return the top-N most semantically relevant past narratives asynchronously.
        Returns an empty list if memory is unavailable or empty.
        """
        import asyncio
        def _sync_query():
            if not self._ensure_ready() or self._collection is None:
                return []
            try:
                count = self._collection.count()
                if count == 0:
                    return []
                results = self._collection.query(
                    query_texts=[text],
                    n_results=min(n, count),
                )
                return results["documents"][0] if results["documents"] else []
            except Exception as e:  # noqa: BLE001
                logger.error("RAG memory: query failed: %s", e)
                return []
        return await asyncio.to_thread(_sync_query)


class NPCMemory:
    """
    In-memory chromadb mapping of NPC names to their specific scene embeddings.
    """

    def __init__(self, base_collection_name: str = "cyoa_npc_memory") -> None:
        self._available: bool = _CHROMA_AVAILABLE
        self._base_name: str = base_collection_name
        self._client: Optional[Any] = None
        self._collections: dict[str, Any] = {}

    def _ensure_ready(self, npc_name: str) -> bool:
        if not self._available:
            return False
        # Create a safe alphanumeric name for chroma collections
        safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()

        if safe_name in self._collections:
            return True
        try:
            if self._client is None:
                self._client = chromadb.Client()
            unique_name = f"{self._base_name}_{safe_name}_{uuid.uuid4().hex[:8]}"
            self._collections[safe_name] = self._client.create_collection(
                name=unique_name,
                metadata={"hnsw:space": "cosine"},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(
                "RAG NPC memory: failed to initialise chromadb for %s: %s", npc_name, e
            )
            self._available = False
            return False

    async def add_async(self, npc_name: str, scene_id: str, narrative: str) -> None:
        import asyncio
        def _sync_add():
            if not self._ensure_ready(npc_name):
                return
            safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()
            if safe_name not in self._collections:
                return

            try:
                self._collections[safe_name].upsert(
                    ids=[scene_id],
                    documents=[narrative],
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "RAG NPC memory: failed to add scene %s for %s: %s",
                    scene_id,
                    npc_name,
                    e,
                )
        await asyncio.to_thread(_sync_add)

    async def query_async(self, npc_name: str, text: str, n: int = 2) -> list[str]:
        import asyncio
        def _sync_query():
            if not self._ensure_ready(npc_name):
                return []
            safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()
            if safe_name not in self._collections:
                return []

            try:
                collection = self._collections[safe_name]
                count = collection.count()
                if count == 0:
                    return []
                results = collection.query(
                    query_texts=[text],
                    n_results=min(n, count),
                )
                return results["documents"][0] if results["documents"] else []
            except Exception as e:  # noqa: BLE001
                logger.error("RAG NPC memory: query failed for %s: %s", npc_name, e)
                return []
        return await asyncio.to_thread(_sync_query)
