"""
rag_memory.py — Semantic narrative memory backed by chromadb.

Stores past scene narratives as vector embeddings so that the most
semantically relevant past scenes can be retrieved and injected into the
LLM prompt as a "memory" block, giving it long-term story coherence
beyond the sliding context window.
"""

import logging
import uuid
from collections import deque
from typing import Any

from cyoa.core.observability import DBObservedSession

__all__ = ["NarrativeMemory", "NPCMemory"]

try:
    import chromadb

    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False

logger = logging.getLogger(__name__)


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
        self._client: Any | None = None
        self._collection: Any | None = None
        self._init_attempted: bool = False

        # Fallback: maintain a small circular buffer of recent narratives
        # if ChromaDB is missing. This ensures the prompt still gets some
        # 'memories' context even in degraded mode.
        self._fallback: deque[str] = deque(maxlen=10)

    @property
    def is_online(self) -> bool:
        """Returns True if ChromaDB is available and ready."""
        return self._available and (self._collection is not None or not self._init_attempted)

    def verify_availability(self) -> bool:
        """Explicitly attempt to initialize ChromaDB and return status."""
        return self._ensure_ready()

    def _ensure_ready(self) -> bool:
        """Create the chroma client and collection on first use. Returns False if unavailable."""
        if not self._available:
            return False
        if self._collection is not None:
            return True

        if self._init_attempted:
            return False

        self._init_attempted = True
        try:
            # chromadb.Client() with no settings uses an ephemeral in-memory DB.
            self._client = chromadb.Client()
            unique_name = f"{self._collection_name}_{uuid.uuid4().hex[:8]}"
            self._collection = self._client.create_collection(
                name=unique_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("RAG memory: chroma client initialised (collection: %s)", unique_name)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "RAG memory: failed to initialise chromadb. Falling back to basic memory. Error: %s",
                e,
            )
            self._available = False
            return False

    def close(self) -> None:
        """Release ChromaDB resources. Safe to call even if never initialised."""
        try:
            if self._collection is not None and self._client is not None:
                self._client.delete_collection(self._collection.name)
        except Exception:  # noqa: BLE001
            pass
        self._collection = None
        self._client = None

    async def add_async(self, scene_id: str, narrative: str) -> None:
        """Embed and store a scene narrative asynchronously."""
        # Update fallback buffer regardless of Chroma availability
        self._fallback.append(narrative)

        if not self._available:
            return

        import asyncio

        def _sync_add() -> None:
            if not self._ensure_ready() or self._collection is None:
                return
            with DBObservedSession("chroma", "add_narrative"):
                try:
                    self._collection.upsert(
                        ids=[scene_id],
                        documents=[narrative],
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG memory: failed to add scene %s: %s", scene_id, e)
                    self._available = False

        await asyncio.to_thread(_sync_add)

    async def query_async(self, text: str, n: int = 3) -> list[str]:
        """
        Return the top-N most semantically relevant past narratives asynchronously.
        Returns recent history from fallback buffer if chromadb is unavailable.
        """
        if not self._available:
            # Basic fallback: return latest N items from our buffer.
            # While not 'semantic', it maintains the presence of a Memory block.
            return list(self._fallback)[-n:]

        import asyncio

        def _sync_query() -> list[str]:
            if not self._ensure_ready() or self._collection is None:
                return list(self._fallback)[-n:]
            with DBObservedSession("chroma", "query_narrative"):
                try:
                    count = self._collection.count()
                    if count == 0:
                        return list(self._fallback)[-n:]
                    results = self._collection.query(
                        query_texts=[text],
                        n_results=min(n, count),
                    )
                    return results["documents"][0] if results["documents"] else []
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG memory: query failed: %s", e)
                    return list(self._fallback)[-n:]

        return await asyncio.to_thread(_sync_query)


class NPCMemory:
    """
    In-memory chromadb mapping of NPC names to their specific scene embeddings.
    """

    def __init__(self, base_collection_name: str = "cyoa_npc_memory") -> None:
        self._available: bool = _CHROMA_AVAILABLE
        self._base_name: str = base_collection_name
        self._client: Any | None = None
        self._collections: dict[str, Any] = {}
        self._init_attempted: bool = False

        # Fallback: Dictionary of NPC names to their recent scene buffers
        self._fallbacks: dict[str, deque[str]] = {}

    @property
    def is_online(self) -> bool:
        """Returns True if the basic Chroma client is available."""
        return self._available

    def verify_availability(self) -> bool:
        """Explicitly attempt to initialize the basic Chroma client and return status."""
        return self._ensure_ready("test_init")

    def _ensure_ready(self, npc_name: str) -> bool:
        if not self._available:
            return False
        # Create a safe alphanumeric name for chroma collections
        safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()

        if safe_name in self._collections:
            return True

        if self._init_attempted and self._client is None:
            return False

        try:
            if self._client is None:
                self._init_attempted = True
                self._client = chromadb.Client()
                logger.info("RAG NPC memory: chroma client initialised")

            unique_name = f"{self._base_name}_{safe_name}_{uuid.uuid4().hex[:8]}"
            self._collections[safe_name] = self._client.create_collection(
                name=unique_name,
                metadata={"hnsw:space": "cosine"},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "RAG NPC memory: failed to initialise chromadb for %s. Error: %s",
                npc_name,
                e,
            )
            # If the client itself failed, mark global unavailability
            if self._client is None:
                self._available = False
            return False

    def close(self) -> None:
        """Release all ChromaDB collections. Safe to call even if never initialised."""
        try:
            if self._client is not None:
                for safe_name, coll in self._collections.items():
                    try:
                        self._client.delete_collection(coll.name)
                    except Exception:
                        pass
        except Exception:  # noqa: BLE001
            pass
        self._collections.clear()
        self._client = None

    async def add_async(self, npc_name: str, scene_id: str, narrative: str) -> None:
        # Update fallback buffer for this NPC
        if npc_name not in self._fallbacks:
            self._fallbacks[npc_name] = deque(maxlen=5)
        self._fallbacks[npc_name].append(narrative)

        if not self._available:
            return

        import asyncio

        def _sync_add() -> None:
            if not self._ensure_ready(npc_name):
                return
            safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()
            if safe_name not in self._collections:
                return

            with DBObservedSession("chroma", f"add_npc_{safe_name}"):
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
                    self._available = False

        await asyncio.to_thread(_sync_add)

    async def query_async(self, npc_name: str, text: str, n: int = 2) -> list[str]:
        if not self._available:
            # Basic fallback for NPC memory
            buffer = self._fallbacks.get(npc_name, deque())
            return list(buffer)[-n:]

        import asyncio

        def _sync_query() -> list[str]:
            if not self._ensure_ready(npc_name):
                buffer = self._fallbacks.get(npc_name, deque())
                return list(buffer)[-n:]

            safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()
            if safe_name not in self._collections:
                buffer = self._fallbacks.get(npc_name, deque())
                return list(buffer)[-n:]

            with DBObservedSession("chroma", f"query_npc_{safe_name}"):
                try:
                    collection = self._collections[safe_name]
                    count = collection.count()
                    if count == 0:
                        buffer = self._fallbacks.get(npc_name, deque())
                        return list(buffer)[-n:]

                    results = collection.query(
                        query_texts=[text],
                        n_results=min(n, count),
                    )
                    return results["documents"][0] if results["documents"] else []
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG NPC memory: query failed for %s: %s", npc_name, e)
                    buffer = self._fallbacks.get(npc_name, deque())
                    return list(buffer)[-n:]

        return await asyncio.to_thread(_sync_query)
