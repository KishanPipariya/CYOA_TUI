import logging
import os
import uuid
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

from cyoa.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from cyoa.core.constants import DEFAULT_NEO4J_URI
from cyoa.core.observability import DBObservedSession

logger = logging.getLogger(__name__)


class CYOAGraphDB:
    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the connection to Neo4j. Reads credentials from env vars if not provided."""
        uri = uri or os.getenv("NEO4J_URI", DEFAULT_NEO4J_URI)
        user = user or os.getenv("NEO4J_USER")
        password = password or os.getenv("NEO4J_PASSWORD")
        self.driver = None  # Initialize to None

        self.cb = CircuitBreaker("Neo4j", failure_threshold=3, reset_timeout=30.0)

        try:
            # Cast uri to str because the Neo4j driver expects a non-None URI
            uri_str = str(uri)
            self.driver = GraphDatabase.driver(
                uri_str, auth=(str(user), str(password)), connection_timeout=2.0
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Failed to create Neo4j driver. Graph persistence disabled. Error: {e}"
            )
            self.driver = None

    async def verify_connectivity_async(self) -> bool:
        """
        Verify connectivity to Neo4j asynchronously.
        Returns True if successful, False otherwise.
        """
        if not self.driver:
            return False

        import asyncio
        try:
            await asyncio.to_thread(self.driver.verify_connectivity)
            logger.info("Successfully connected to Neo4j.")
            return True
        except AuthError:
            logger.error(
                "Failed to connect to Neo4j: Authentication failed. Check username and password."
            )
            self.driver = None
            self.cb._on_failure(Exception("Authentication failed"))
            return False
        except (ServiceUnavailable, Exception) as e:  # noqa: BLE001
            logger.warning(
                f"Graph DB is offline. Proceeding without graph persistence. Error: {e}"
            )
            self.cb._on_failure(e)
            self.driver = None
            return False

    @property
    def is_online(self) -> bool:
        """Returns True if the database connection is active and the circuit is not open."""
        return self.driver is not None and self.cb.is_available

    def close(self) -> None:
        """Close the database connection."""
        if self.driver:
            self.driver.close()

    # ── Fix #1: Restored method (was trapped in dead docstring in close()) ──

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
                with self.driver.session() as session:
                    result = session.run(title_exists_query, base_title=generated_title)
                    existing_titles = [record["title"] for record in result]

                    final_title = generated_title
                    if existing_titles:
                        max_mod = 0
                        for title in existing_titles:
                            if title == generated_title:
                                max_mod = max(max_mod, 1)
                            elif title.startswith(f"{generated_title} ("):
                                try:
                                    mod_str = title[len(generated_title) + 2 : -1]
                                    max_mod = max(max_mod, int(mod_str))
                                except ValueError:
                                    pass

                        if max_mod > 0:
                            final_title = f"{generated_title} ({max_mod + 1})"

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
            return self.cb.call(_work)
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
    ) -> str:
        """Creates a Scene node in the graph, links it to its Story, and returns its UUID."""
        scene_id = str(uuid.uuid4())

        if not self.is_online:
            return scene_id

        def _work() -> str:
            query = """
            MATCH (story:Story {title: $story_title})
            CREATE (s:Scene {
                id: $scene_id,
                narrative: $narrative,
                available_choices: $available_choices,
                story_title: $story_title,
                player_stats: $player_stats,
                inventory: $inventory
            })
            CREATE (s)-[:BELONGS_TO]->(story)
            RETURN s.id AS scene_id
            """
            with DBObservedSession("neo4j", "create_scene_node") as session_obs:
                with self.driver.session() as session:
                    result = session.run(
                        query,
                        scene_id=scene_id,
                        narrative=narrative,
                        available_choices=available_choices,
                        story_title=story_title,
                        player_stats=player_stats or {"health": 100, "gold": 0, "reputation": 0},
                        inventory=inventory or [],
                    )
                    record = result.single()
                    if record is None:
                        return scene_id

                    final_id = str(record["scene_id"])
                    if session_obs.span:
                        session_obs.span.set_attribute("scene.id", final_id)
                    return final_id

        try:
            return self.cb.call(_work)
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
            CREATE (source)-[r:LEADS_TO {action_text: $choice_text}]->(target)
            """
            with DBObservedSession("neo4j", "create_choice_edge"):
                with self.driver.session() as session:
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
    ) -> str:
        """
        Writes a new scene node (and optional edge from previous scene) to Neo4j.
        Runs the blocking DB operations in a worker thread and returns the new scene ID.
        """
        import asyncio

        def _write() -> str:
            new_scene_id = self.create_scene_node(
                narrative, available_choices, story_title, player_stats, inventory
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
                "RETURN nodes(path) AS scenes, relationships(path) AS choices"
            )
            with self.driver.session() as session:
                result = session.run(query, current_id=current_scene_id)
                record = result.single()
                if not record:
                    return None

                scenes = [
                    {
                        "id": n["id"],
                        "narrative": n["narrative"],
                        "available_choices": n.get("available_choices", []),
                        "player_stats": dict(n.get("player_stats", {})),
                        "inventory": list(n.get("inventory", [])),
                    }
                    for n in record["scenes"]
                ]
                choices = [r["action_text"] for r in record["choices"]]

                return {"scenes": scenes, "choices": choices}

        try:
            return self.cb.call(_work)
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j history retrieval skipped: {e}")
            return None

    def get_all_story_scenes(self, story_title: str) -> list[dict]:
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

        def _work() -> list[dict]:
            # Single Cypher path query — one round-trip instead of N+1.
            # The OPTIONAL MATCH on the outgoing edge lets us capture
            # choice_taken for every node including the last (leaf) one.
            query = """
            MATCH (story:Story {title: $story_title})<-[:BELONGS_TO]-(root:Scene)
            WHERE NOT ()-[:LEADS_TO]->(root)
            MATCH path = (root)-[:LEADS_TO*0..]->(scene:Scene)
            OPTIONAL MATCH (scene)-[edge:LEADS_TO]->(next:Scene)
            RETURN scene.id AS id,
                   scene.narrative AS narrative,
                   edge.action_text AS choice_taken
            ORDER BY length(path)
            """
            with DBObservedSession("neo4j", "get_all_story_scenes"):
                with self.driver.session() as session:
                    try:
                        result = session.run(query, story_title=story_title)
                        return [
                            {
                                "id": record["id"],
                                "narrative": record["narrative"],
                                "choice_taken": record["choice_taken"],
                            }
                            for record in result
                        ]
                    except Exception as e:  # noqa: BLE001
                        logger.warning("get_all_story_scenes query failed: %s", e)
                        return []

        try:
            return self.cb.call(_work)
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j scenes retrieval skipped: {e}")
            return []


    def get_story_tree(self, story_title: str) -> dict:
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

        def _work() -> dict:
            query = """
            MATCH (story:Story {title: $story_title})<-[:BELONGS_TO]-(scene:Scene)
            OPTIONAL MATCH (scene)-[r:LEADS_TO]->(next:Scene)
            RETURN scene.id AS id, scene.narrative AS narrative,
                   next.id AS next_id, r.action_text AS choice
            """
            nodes = {}
            edges: dict[str, list[dict[str, Any]]] = {}
            has_incoming = set()

            with self.driver.session() as session:
                result = session.run(query, story_title=story_title)
                for record in result:
                    sid = record["id"]
                    nxt = record["next_id"]
                    if sid not in nodes:
                        nodes[sid] = {"id": sid, "narrative": record["narrative"]}
                    if sid not in edges:
                        edges[sid] = []
                    if nxt:
                        edges[sid].append({"target_id": nxt, "choice": record["choice"]})
                        has_incoming.add(nxt)

            if not nodes:
                return {}

            root_id = None
            for n in nodes:
                if n not in has_incoming:
                    root_id = n
                    break

            return {"root_id": root_id, "nodes": nodes, "edges": edges}

        try:
            return self.cb.call(_work)
        except (CircuitBreakerOpenError, Exception) as e:  # noqa: BLE001
            logger.warning(f"Neo4j story tree retrieval skipped: {e}")
            return {}



# Example Usage removed for production cleanup.
