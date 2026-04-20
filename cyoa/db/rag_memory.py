"""
rag_memory.py — Semantic narrative memory backed by chromadb.

Stores past scene narratives as vector embeddings so that the most
semantically relevant past scenes can be retrieved and injected into the
LLM prompt as a "memory" block, giving it long-term story coherence
beyond the sliding context window.
"""

import logging
import os
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from cyoa.core.observability import DBObservedSession

__all__ = ["NarrativeMemory", "NPCMemory"]

try:
    import chromadb

    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False

logger = logging.getLogger(__name__)


def _env_flag_enabled(name: str) -> bool:
    return name.strip() != "" and os.getenv(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_rag_diagnostics_enabled() -> bool:
    return _env_flag_enabled("CYOA_ENABLE_RAG")


@dataclass(slots=True)
class _RetryState:
    """Tracks temporary Chroma outages and schedules recovery probes."""

    max_failures: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    reprobe_interval_seconds: float = 60.0
    clock: Callable[[], float] = field(default=monotonic)
    consecutive_failures: int = 0
    retry_at: float = 0.0
    unavailable_since: float | None = None

    def can_attempt(self) -> bool:
        now = self.clock()
        if self.unavailable_since is not None:
            if (now - self.unavailable_since) >= self.reprobe_interval_seconds:
                self.reset()
                return True
            return False
        return now >= self.retry_at

    def record_success(self) -> None:
        self.reset()

    def record_failure(self) -> None:
        now = self.clock()
        self.consecutive_failures += 1
        backoff = min(
            self.base_backoff_seconds * (2 ** (self.consecutive_failures - 1)),
            self.max_backoff_seconds,
        )
        self.retry_at = now + backoff
        if self.consecutive_failures >= self.max_failures:
            self.unavailable_since = now

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.retry_at = 0.0
        self.unavailable_since = None


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
        self._retry_state = _RetryState()

        # Fallback: maintain a small circular buffer of recent narratives
        # if ChromaDB is missing. This ensures the prompt still gets some
        # 'memories' context even in degraded mode.
        self._fallback: deque[str] = deque(maxlen=10)

    @property
    def is_online(self) -> bool:
        """Returns True if ChromaDB is available and ready."""
        return self._available and (
            self._collection is not None
            or (not self._init_attempted and self._retry_state.can_attempt())
            or self._retry_state.can_attempt()
        )

    def verify_availability(self) -> bool:
        """Explicitly attempt to initialize ChromaDB and return status."""
        return self._ensure_ready(force=True)

    def _reset_client_state(self) -> None:
        self._collection = None
        self._client = None
        self._init_attempted = False

    def _mark_failure(self, context: str, error: Exception) -> None:
        self._reset_client_state()
        self._retry_state.record_failure()
        if self._retry_state.unavailable_since is not None:
            logger.warning(
                "RAG memory: %s failed repeatedly; memory marked unavailable until next probe. Error: %s",
                context,
                error,
            )
        else:
            retry_in = max(self._retry_state.retry_at - self._retry_state.clock(), 0.0)
            logger.warning(
                "RAG memory: %s failed; retrying after %.1fs. Error: %s",
                context,
                retry_in,
                error,
            )

    def _ensure_ready(self, *, force: bool = False) -> bool:
        """Create the chroma client and collection on first use. Returns False if unavailable."""
        if not self._available:
            return False
        if self._collection is not None:
            return True

        if not force and not self._retry_state.can_attempt():
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
            self._retry_state.record_success()
            logger.info("RAG memory: chroma client initialised (collection: %s)", unique_name)
            return True
        except Exception as e:  # noqa: BLE001
            self._mark_failure("initialise chromadb", e)
            return False

    def close(self) -> None:
        """Release ChromaDB resources. Safe to call even if never initialised."""
        try:
            if self._collection is not None and self._client is not None:
                self._client.delete_collection(self._collection.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to delete ChromaDB collection during close: %s", e)
        self._reset_client_state()
        self._retry_state.reset()

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
                    self._retry_state.record_success()
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG memory: failed to add scene %s: %s", scene_id, e)
                    self._mark_failure(f"add scene {scene_id}", e)

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
                    self._retry_state.record_success()
                    return results["documents"][0] if results["documents"] else []
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG memory: query failed: %s", e)
                    self._mark_failure("query chromadb", e)
                    return list(self._fallback)[-n:]

        return await asyncio.to_thread(_sync_query)

    async def get_recent_async(self, n: int = 2, *, exclude_text: str | None = None) -> list[str]:
        """Return the most recent prior narratives for short-term continuity."""
        entries = [item for item in self._fallback if item and item != exclude_text]
        if not entries:
            return []
        return list(reversed(entries[-n:]))


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
        self._retry_state = _RetryState()

        # Fallback: Dictionary of NPC names to their recent scene buffers
        self._fallbacks: dict[str, deque[str]] = {}

    @property
    def is_online(self) -> bool:
        """Returns True if the basic Chroma client is available."""
        return self._available and (
            self._client is not None or not self._init_attempted or self._retry_state.can_attempt()
        )

    def verify_availability(self) -> bool:
        """Explicitly attempt to initialize the basic Chroma client and return status."""
        return self._ensure_ready("test_init", force=True)

    def _reset_client_state(self) -> None:
        self._collections.clear()
        self._client = None
        self._init_attempted = False

    def _mark_failure(self, npc_name: str, context: str, error: Exception) -> None:
        self._reset_client_state()
        self._retry_state.record_failure()
        if self._retry_state.unavailable_since is not None:
            logger.warning(
                "RAG NPC memory: %s for %s failed repeatedly; memory marked unavailable until next probe. Error: %s",
                context,
                npc_name,
                error,
            )
        else:
            retry_in = max(self._retry_state.retry_at - self._retry_state.clock(), 0.0)
            logger.warning(
                "RAG NPC memory: %s for %s failed; retrying after %.1fs. Error: %s",
                context,
                npc_name,
                retry_in,
                error,
            )

    def _ensure_ready(self, npc_name: str, *, force: bool = False) -> bool:
        if not self._available:
            return False
        # Create a safe alphanumeric name for chroma collections
        safe_name = "".join(c if c.isalnum() else "_" for c in npc_name).lower()

        if safe_name in self._collections:
            return True

        if not force and not self._retry_state.can_attempt():
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
            self._retry_state.record_success()
            return True
        except Exception as e:  # noqa: BLE001
            self._mark_failure(npc_name, "initialise chromadb", e)
            return False

    def close(self) -> None:
        """Release all ChromaDB collections. Safe to call even if never initialised."""
        try:
            if self._client is not None:
                for safe_name, coll in self._collections.items():
                    try:
                        self._client.delete_collection(coll.name)
                    except Exception as e:
                        logger.debug("Failed to delete NPC collection %s: %s", safe_name, e)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to close NPC memory client: %s", e)
        self._reset_client_state()
        self._retry_state.reset()

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
                    self._retry_state.record_success()
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "RAG NPC memory: failed to add scene %s for %s: %s",
                        scene_id,
                        npc_name,
                        e,
                    )
                    self._mark_failure(npc_name, f"add scene {scene_id}", e)

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
                    self._retry_state.record_success()
                    return results["documents"][0] if results["documents"] else []
                except Exception as e:  # noqa: BLE001
                    logger.error("RAG NPC memory: query failed for %s: %s", npc_name, e)
                    self._mark_failure(npc_name, "query chromadb", e)
                    buffer = self._fallbacks.get(npc_name, deque())
                    return list(buffer)[-n:]

        return await asyncio.to_thread(_sync_query)
