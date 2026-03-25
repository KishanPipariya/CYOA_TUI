import logging
import os
import uuid
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

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

        try:
            # Cast uri to str because the Neo4j driver expects a non-None URI
            uri_str = str(uri)
            self.driver = GraphDatabase.driver(
                uri_str, auth=(str(user), str(password)), connection_timeout=2.0
            )
            # Verify connectivity immediately to fail fast.
            self.driver.verify_connectivity()
            logger.info("Successfully connected to Neo4j.")
        except ServiceUnavailable as e:
            logger.warning(f"Graph DB is offline. Proceeding without graph persistence. Error: {e}")
            self.driver = None
        except AuthError:
            logger.error(
                "Failed to connect to Neo4j: Authentication failed. Check username and password."
            )
            self.driver = None
        except Exception as e:  # noqa: BLE001
            logger.error(f"Unexpected Graph DB connection error: {e}")
            self.driver = None

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
        if not self.driver:
            return generated_title

        # Check if the title exists, and if so, append a modifier
        query_check = (
            "MATCH (s:Story) WHERE s.title STARTS WITH $base_title RETURN s.title AS title"
        )

        final_title = generated_title
        with DBObservedSession("neo4j", "create_story_node") as session_obs:
            with self.driver.session() as session:
                result = session.run(query_check, base_title=generated_title)
                existing_titles = [record["title"] for record in result]

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

    def create_scene_node(
        self, narrative: str, available_choices: list[str], story_title: str
    ) -> str:
        """Creates a Scene node in the graph, links it to its Story, and returns its UUID."""
        scene_id = str(uuid.uuid4())

        if not self.driver:
            return scene_id

        query = """
        MATCH (story:Story {title: $story_title})
        CREATE (s:Scene {
            id: $scene_id,
            narrative: $narrative,
            available_choices: $available_choices,
            story_title: $story_title
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
                )
                record = result.single()
                if record is None:
                    return scene_id
                
                final_id = str(record["scene_id"])
                if session_obs.span:
                    session_obs.span.set_attribute("scene.id", final_id)
                return final_id

    def create_choice_edge(
        self, source_scene_id: str, target_scene_id: str, choice_text: str
    ) -> None:
        """Creates a LEADS_TO relationship between two scenes based on a choice."""
        if not self.driver:
            return

        query = """
        MATCH (source:Scene {id: $source_id})
        MATCH (target:Scene {id: $target_id})
        CREATE (source)-[r:LEADS_TO {action_text: $choice_text}]->(target)
        RETURN r
        """

        with DBObservedSession("neo4j", "create_choice_edge"):
            with self.driver.session() as session:
                session.run(
                    query,
                    source_id=source_scene_id,
                    target_id=target_scene_id,
                    choice_text=choice_text,
                )

    async def save_scene_async(
        self,
        narrative: str,
        available_choices: list[str],
        story_title: str,
        source_scene_id: str | None,
        choice_text: str | None,
    ) -> str:
        """
        Writes a new scene node (and optional edge from previous scene) to Neo4j.
        Runs the blocking DB operations in a worker thread and returns the new scene ID.
        """
        import asyncio

        def _write() -> str:
            new_scene_id = self.create_scene_node(narrative, available_choices, story_title)
            if source_scene_id and choice_text:
                self.create_choice_edge(source_scene_id, new_scene_id, choice_text)
            return new_scene_id

        return await asyncio.to_thread(_write)

    def get_scene_history_path(self, current_scene_id: str) -> dict[str, Any] | None:
        """
        Retrieves the path of scenes that led to the current scene.
        """
        if not self.driver:
            return None

        query = """
        MATCH path = (start:Scene)-[:LEADS_TO*]->(current:Scene {id: $current_id})
        WHERE NOT ()-[:LEADS_TO]->(start)
        RETURN nodes(path) AS scenes, relationships(path) AS choices
        """

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
                }
                for n in record["scenes"]
            ]
            choices = [r["action_text"] for r in record["choices"]]

            return {"scenes": scenes, "choices": choices}

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
        if not self.driver:
            return []

        # Find the root scene (no incoming LEADS_TO within this story)
        query = """
        MATCH (story:Story {title: $story_title})<-[:BELONGS_TO]-(scene:Scene)
        WHERE NOT ()-[:LEADS_TO]->(scene)
        RETURN scene.id AS id, scene.narrative AS narrative
        LIMIT 1
        """

        with self.driver.session() as session:
            result = session.run(query, story_title=story_title)
            root = result.single()
            if not root:
                return []

            ordered = []
            current_id = root["id"]
            current_narrative = root["narrative"]

            # Walk forward along LEADS_TO edges
            while current_id:
                edge_query = """
                MATCH (s:Scene {id: $scene_id})-[r:LEADS_TO]->(next:Scene)
                RETURN r.action_text AS choice, next.id AS next_id,
                       next.narrative AS next_narrative
                LIMIT 1
                """
                edge_result = session.run(edge_query, scene_id=current_id)
                edge_record = edge_result.single()

                if edge_record:
                    ordered.append(
                        {
                            "id": current_id,
                            "narrative": current_narrative,
                            "choice_taken": edge_record["choice"],
                        }
                    )
                    current_id = edge_record["next_id"]
                    current_narrative = edge_record["next_narrative"]
                else:
                    # Leaf / current scene — no outgoing edge
                    ordered.append(
                        {
                            "id": current_id,
                            "narrative": current_narrative,
                            "choice_taken": None,
                        }
                    )
                    break

        return ordered

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
        if not self.driver:
            return {}

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


# Example Usage removed for production cleanup.
