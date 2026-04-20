import logging
import os
import uuid
from collections.abc import Iterable
from typing import Any, TypedDict, cast

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

from cyoa.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from cyoa.core.constants import DEFAULT_NEO4J_URI
from cyoa.core.observability import DBObservedSession

logger = logging.getLogger(__name__)


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_graph_db_enabled() -> bool:
    return _env_flag_enabled("CYOA_ENABLE_GRAPH_DB") or any(
        os.getenv(name)
        for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
    )


class StoryTreeEdge(TypedDict):
    target_id: str
    choice: str | None


class StoryTreeNode(TypedDict):
    id: str
    narrative: str
    mood: str


class CYOAGraphDB:
    SCHEMA_MIGRATION_STATEMENTS: tuple[str, ...] = (
        "CREATE CONSTRAINT story_id_unique IF NOT EXISTS FOR (s:Story) REQUIRE s.id IS UNIQUE;",
        "CREATE CONSTRAINT story_title_unique IF NOT EXISTS FOR (s:Story) REQUIRE s.title IS UNIQUE;",
        "CREATE CONSTRAINT scene_id_unique IF NOT EXISTS FOR (s:Scene) REQUIRE s.id IS UNIQUE;",
        "CREATE INDEX scene_story_title IF NOT EXISTS FOR (s:Scene) ON (s.story_title);",
    )

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the connection to Neo4j. Reads credentials from env vars if not provided."""
        self.enabled = any(value is not None for value in (uri, user, password)) or is_graph_db_enabled()
        uri = uri or os.getenv("NEO4J_URI", DEFAULT_NEO4J_URI)
        user = user or os.getenv("NEO4J_USER")
        password = password or os.getenv("NEO4J_PASSWORD")
        self.driver: Any | None = None
        self.cb: CircuitBreaker = CircuitBreaker("Neo4j", failure_threshold=3, reset_timeout=30.0)

        if not self.enabled:
            return

        try:
            # Cast uri to str because the Neo4j driver expects a non-None URI
            uri_str = str(uri)
            self.driver = GraphDatabase.driver(
                uri_str, auth=(str(user or ""), str(password or "")), connection_timeout=1.0
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Failed to create Neo4j driver. Graph persistence disabled. Error: {e}"
            )
            self.driver = None

    @staticmethod
    def _normalize_player_stats(player_stats: dict[str, int] | None) -> dict[str, int]:
        base_stats = {"health": 100, "gold": 0, "reputation": 0}
        if player_stats:
            base_stats.update(player_stats)
        return base_stats

    @classmethod
    def _scene_node_player_stats(cls, node: Any) -> dict[str, int]:
        return cls._normalize_player_stats(
            {
                key: value
                for key, value in {
                    "health": node.get("player_health"),
                    "gold": node.get("player_gold"),
                    "reputation": node.get("player_reputation"),
                }.items()
                if isinstance(value, int)
            }
        )

    async def verify_connectivity_async(self) -> bool:
        """
        Verify connectivity to Neo4j asynchronously.
        Returns True if successful, False otherwise.
        """
        if not self.driver:
            return False

        import asyncio
        try:
            await asyncio.to_thread(self.cb.call, self.driver.verify_connectivity)
            logger.info("Successfully connected to Neo4j.")
            return True
        except AuthError:
            logger.error(
                "Failed to connect to Neo4j: Authentication failed. Check username and password."
            )
            return False
        except (CircuitBreakerOpenError, ServiceUnavailable, Exception) as e:  # noqa: BLE001
            logger.warning(
                f"Graph DB is offline. Proceeding without graph persistence. Error: {e}"
            )
            return False

    @property
    def is_online(self) -> bool:
        """Returns True if the database connection is active and the circuit is not open."""
        return self.driver is not None and self.cb.is_available

    def close(self) -> None:
        """Close the database connection."""
        if self.driver:
            self.driver.close()

    @staticmethod
    def _parse_title_modifier(existing_title: str, base_title: str) -> int | None:
        if existing_title == base_title:
            return 1

        prefix = f"{base_title} ("
        if not existing_title.startswith(prefix) or not existing_title.endswith(")"):
            return None

        try:
            return int(existing_title[len(prefix) : -1])
        except ValueError:
            return None

    @classmethod
    def _resolve_story_title_collision(
        cls, base_title: str, existing_titles: Iterable[str]
    ) -> str:
        highest_modifier = max(
            (
                modifier
                for modifier in (
                    cls._parse_title_modifier(existing_title, base_title)
                    for existing_title in existing_titles
                )
                if modifier is not None
            ),
            default=0,
        )
        if highest_modifier == 0:
            return base_title
        return f"{base_title} ({highest_modifier + 1})"

    @staticmethod
    def _pick_story_root(
        nodes: dict[str, StoryTreeNode], has_incoming: set[str]
    ) -> str | None:
        for node_id in nodes:
            if node_id not in has_incoming:
                return node_id
        return next(iter(nodes), None)

    @staticmethod
    def _append_unique_edge(
        raw_edges: dict[str, list[StoryTreeEdge]],
        scene_id: str,
        edge: StoryTreeEdge,
    ) -> None:
        existing_edges = raw_edges.setdefault(scene_id, [])
        if edge not in existing_edges:
            existing_edges.append(edge)

    @classmethod
    def schema_migration_statements(cls) -> tuple[str, ...]:
        """Return the recommended Neo4j schema hardening statements."""
        return cls.SCHEMA_MIGRATION_STATEMENTS

    @staticmethod
    def _build_story_tree_payload(records: Iterable[Any]) -> dict[str, Any]:
        nodes: dict[str, StoryTreeNode] = {}
        raw_edges: dict[str, list[StoryTreeEdge]] = {}
        has_incoming: set[str] = set()

        for record in records:
            scene_id = record["id"]
            next_id = record["next_id"]

            nodes.setdefault(
                scene_id,
                {
                    "id": scene_id,
                    "narrative": record["narrative"],
                    "mood": record.get("mood", "default"),
                },
            )
            raw_edges.setdefault(scene_id, [])

            if not next_id:
                continue

            CYOAGraphDB._append_unique_edge(
                raw_edges,
                scene_id,
                {"target_id": next_id, "choice": record["choice"]},
            )
            has_incoming.add(next_id)

        for scene_edges in raw_edges.values():
            scene_edges.sort(key=lambda edge: ((edge["choice"] or ""), edge["target_id"]))

        root_id = CYOAGraphDB._pick_story_root(nodes, has_incoming)
        if root_id is None:
            return {}

        if root_id in has_incoming:
            logger.warning(
                "Story tree for root candidate %s has no incoming-free root; pruning cycles from fallback root.",
                root_id,
            )

        edges: dict[str, list[StoryTreeEdge]] = {scene_id: [] for scene_id in nodes}

        def walk(scene_id: str, active_path: set[str]) -> None:
            active_path.add(scene_id)
            for edge in raw_edges.get(scene_id, []):
                target_id = edge["target_id"]
                if target_id in active_path:
                    logger.warning(
                        "Skipping cyclic story tree edge %s -> %s for story root %s",
                        scene_id,
                        target_id,
                        root_id,
                    )
                    continue
                edges[scene_id].append(edge)
                walk(target_id, active_path)
            active_path.remove(scene_id)

        walk(root_id, set())
        return {"root_id": root_id, "nodes": nodes, "edges": edges}

    @classmethod
    def _build_linear_story_path(cls, records: Iterable[Any]) -> list[dict[str, Any]]:
        payload = cls._build_story_tree_payload(records)
        root_id = payload.get("root_id")
        nodes = cast(dict[str, StoryTreeNode], payload.get("nodes", {}))
        edges = cast(dict[str, list[StoryTreeEdge]], payload.get("edges", {}))
        if not root_id or root_id not in nodes:
            return []

        path: list[dict[str, Any]] = []
        current_id = root_id
        visited: set[str] = set()

        while current_id not in visited and current_id in nodes:
            visited.add(current_id)
            node = nodes[current_id]
            outgoing = edges.get(current_id, [])
            path.append(
                {
                    "id": node["id"],
                    "narrative": node["narrative"],
                    "choice_taken": outgoing[0]["choice"] if outgoing else None,
                }
            )
            if not outgoing:
                break

            next_id = outgoing[0]["target_id"]
            if next_id in visited:
                logger.warning(
                    "Stopping linear story traversal at cyclic edge %s -> %s",
                    current_id,
                    next_id,
                )
                break
            current_id = next_id

        return path

    def _require_driver(self) -> Any:
        assert self.driver is not None
        return self.driver

    def create_story_node_and_get_title(self, generated_title: str) -> str:
        """
        Creates a new Story node to act as the root of the graph.
        If the generated_title already exists, appends a numeric modifier.
        Returns the final unique title used.
        """
        if not self.is_online:
            return generated_title

        def _work() -> str:
            # Check if the title exists, and if so, append a modifier
            title_exists_query = (
                "MATCH (s:Story) WHERE s.title STARTS WITH $base_title RETURN s.title AS title"
            )

            with DBObservedSession("neo4j", "create_story_node") as session_obs:
                with self._require_driver().session() as session:
                    result = session.run(title_exists_query, base_title=generated_title)
                    existing_titles = [record["title"] for record in result]
                    final_title = self._resolve_story_title_collision(
                        generated_title, existing_titles
                    )

                    query_create = """
                    CREATE (s:Story {
                        id: $story_id,
                        title: $final_title
                    })
                    RETURN s.title AS title
                    """
                    session.run(query_create, story_id=str(uuid.uuid4()), final_title=final_title)
                    if session_obs.span:
                        session_obs.span.set_attribute("story.title", final_title)
                    return final_title

        try:
            return cast(str, self.cb.call(_work))
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j story creation skipped: {e}")
            return generated_title

    def create_scene_node(
        self,
        narrative: str,
        available_choices: list[str],
        story_title: str,
        player_stats: dict[str, int] | None = None,
        inventory: list[str] | None = None,
        mood: str = "default",
    ) -> str:
        """Creates a Scene node in the graph, links it to its Story, and returns its UUID."""
        scene_id = str(uuid.uuid4())

        if not self.is_online:
            return scene_id

        def _work() -> str:
            normalized_player_stats = self._normalize_player_stats(player_stats)
            query = """
            MATCH (story:Story {title: $story_title})
            CREATE (s:Scene {
                id: $scene_id,
                narrative: $narrative,
                available_choices: $available_choices,
                story_title: $story_title,
                player_health: $player_health,
                player_gold: $player_gold,
                player_reputation: $player_reputation,
                inventory: $inventory,
                mood: $mood
            })
            CREATE (s)-[:BELONGS_TO]->(story)
            RETURN s.id AS scene_id
            """
            with DBObservedSession("neo4j", "create_scene_node") as session_obs:
                with self._require_driver().session() as session:
                    result = session.run(
                        query,
                        scene_id=scene_id,
                        narrative=narrative,
                        available_choices=available_choices,
                        story_title=story_title,
                        player_health=normalized_player_stats["health"],
                        player_gold=normalized_player_stats["gold"],
                        player_reputation=normalized_player_stats["reputation"],
                        inventory=inventory or [],
                        mood=mood,
                    )
                    record = result.single()
                    if record is None:
                        return scene_id

                    final_id = str(record["scene_id"])
                    if session_obs.span:
                        session_obs.span.set_attribute("scene.id", final_id)
                    return final_id

        try:
            return cast(str, self.cb.call(_work))
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j scene creation skipped: {e}")
            return scene_id

    def create_choice_edge(
        self, source_scene_id: str, target_scene_id: str, choice_text: str
    ) -> None:
        """Creates a LEADS_TO relationship between two scenes based on a choice."""
        if not self.is_online:
            return

        def _work() -> None:
            query = """
            MATCH (source:Scene {id: $source_id})
            MATCH (target:Scene {id: $target_id})
            MERGE (source)-[r:LEADS_TO {action_text: $choice_text}]->(target)
            """
            with DBObservedSession("neo4j", "create_choice_edge"):
                with self._require_driver().session() as session:
                    session.run(
                        query,
                        source_id=source_scene_id,
                        target_id=target_scene_id,
                        choice_text=choice_text,
                    )

        try:
            self.cb.call(_work)
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j edge creation skipped: {e}")

    async def save_scene_async(
        self,
        narrative: str,
        available_choices: list[str],
        story_title: str,
        source_scene_id: str | None,
        choice_text: str | None,
        player_stats: dict[str, int] | None = None,
        inventory: list[str] | None = None,
        mood: str = "default",
    ) -> str:
        """
        Writes a new scene node (and optional edge from previous scene) to Neo4j.
        Runs the blocking DB operations in a worker thread and returns the new scene ID.
        """
        import asyncio

        def _write() -> str:
            new_scene_id = self.create_scene_node(
                narrative, available_choices, story_title, player_stats, inventory, mood
            )
            if source_scene_id and choice_text:
                self.create_choice_edge(source_scene_id, new_scene_id, choice_text)
            return new_scene_id

        return await asyncio.to_thread(_write)

    def get_scene_history_path(
        self, current_scene_id: str, max_depth: int = 100
    ) -> dict[str, Any] | None:
        """
        Retrieves the path of scenes that led to the current scene.

        Args:
            current_scene_id: The ID of the scene to trace back from.
            max_depth: Maximum number of hops to traverse (limits wire transfer
                       when the caller only needs up to a certain turn index).
        """
        if not self.is_online:
            return None

        def _work() -> dict[str, Any] | None:
            # Use a parameterised variable-length path so Cypher does the
            # traversal in one round-trip instead of one query per hop.
            query = (
                "MATCH path = (start:Scene)-[:LEADS_TO*.." + str(max_depth) + "]->(current:Scene {id: $current_id})\n"
                "WHERE NOT ()-[:LEADS_TO]->(start)\n"
                "RETURN nodes(path) AS scenes, relationships(path) AS choices\n"
                "ORDER BY length(path) DESC\n"
                "LIMIT 1"
            )
            with self._require_driver().session() as session:
                result = session.run(query, current_id=current_scene_id)
                record = result.single()
                if not record:
                    return None

                scenes = [
                    {
                        "id": n["id"],
                        "narrative": n["narrative"],
                        "available_choices": n.get("available_choices", []),
                        "player_stats": self._scene_node_player_stats(n),
                        "inventory": list(n.get("inventory", [])),
                    }
                    for n in record["scenes"]
                ]
                choices = [r["action_text"] for r in record["choices"]]

                return {"scenes": scenes, "choices": choices}

        try:
            return cast(dict[str, Any] | None, self.cb.call(_work))
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j history retrieval skipped: {e}")
            return None

    def get_all_story_scenes(self, story_title: str) -> list[dict[str, Any]]:
        """
        Returns all scenes for the given story in traversal order (root → leaf),
        following the main path (the first LEADS_TO edge from each scene).

        Each entry:
            { "id": str, "narrative": str, "choice_taken": str | None }

        choice_taken is the action_text the player chose to leave this scene
        (None for the current/last scene).

        Returns [] if the graph is offline or path is empty.
        """
        if not self.is_online:
            return []

        def _work() -> list[dict[str, Any]]:
            query = """
            MATCH (story:Story {title: $story_title})<-[:BELONGS_TO]-(scene:Scene)
            OPTIONAL MATCH (scene)-[r:LEADS_TO]->(next:Scene)
            RETURN scene.id AS id,
                   scene.narrative AS narrative,
                   scene.mood AS mood,
                   next.id AS next_id,
                   r.action_text AS choice
            ORDER BY scene.id, choice, next_id
            """
            with DBObservedSession("neo4j", "get_all_story_scenes"):
                with self._require_driver().session() as session:
                    try:
                        result = session.run(query, story_title=story_title)
                        return self._build_linear_story_path(result)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("get_all_story_scenes query failed: %s", e)
                        return []

        try:
            return cast(list[dict[str, Any]], self.cb.call(_work))
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j scenes retrieval skipped: {e}")
            return []


    def get_story_tree(self, story_title: str) -> dict[str, Any]:
        """
        Returns all nodes and edges for the given story to build a topological tree.
        Format:
        {
          "root_id": "...",
          "nodes": { scene_id: {"narrative": "...", "id": "..."} },
          "edges": { source_id: [ {"target_id": "...", "choice": "..."} ] }
        }
        """
        if not self.is_online:
            return {}

        def _work() -> dict[str, Any]:
            query = """
            MATCH (story:Story {title: $story_title})<-[:BELONGS_TO]-(scene:Scene)
            OPTIONAL MATCH (scene)-[r:LEADS_TO]->(next:Scene)
            RETURN scene.id AS id, scene.narrative AS narrative, scene.mood AS mood,
                   next.id AS next_id, r.action_text AS choice
            """
            with self._require_driver().session() as session:
                result = session.run(query, story_title=story_title)
                return self._build_story_tree_payload(result)

        try:
            return cast(dict[str, Any], self.cb.call(_work))
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j story tree retrieval skipped: {e}")
            return {}



# Example Usage removed for production cleanup.
