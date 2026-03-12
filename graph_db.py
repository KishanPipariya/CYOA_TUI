import uuid
import threading
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable


class CYOAGraphDB:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="cyoa_password"):
        """Initialize the connection to Neo4j."""
        # Add a short connection timeout so the TUI doesn't hang if Neo4j is offline.
        self.driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=2.0
        )

        # Verify connectivity immediately to fail fast.
        # If Neo4j is offline, disable the driver gracefully.
        try:
            self.driver.verify_connectivity()
        except ServiceUnavailable as e:
            print(f"Graph DB is offline. Proceeding without graph persistence. Error: {e}")
            self.driver = None
        except Exception as e:
            print(f"Unexpected Graph DB connection error. Error: {e}")
            self.driver = None

    def close(self):
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
        query_check = "MATCH (s:Story) WHERE s.title STARTS WITH $base_title RETURN s.title AS title"

        final_title = generated_title
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
                            mod_str = title[len(generated_title) + 2:-1]
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

        return final_title

    def create_scene_node(self, narrative: str, available_choices: list[str], story_title: str) -> str:
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

        with self.driver.session() as session:
            result = session.run(
                query,
                scene_id=scene_id,
                narrative=narrative,
                available_choices=available_choices,
                story_title=story_title
            )
            return result.single()["scene_id"]

    def create_choice_edge(self, source_scene_id: str, target_scene_id: str, choice_text: str):
        """Creates a LEADS_TO relationship between two scenes based on a choice."""
        if not self.driver:
            return

        query = """
        MATCH (source:Scene {id: $source_id})
        MATCH (target:Scene {id: $target_id})
        CREATE (source)-[r:LEADS_TO {action_text: $choice_text}]->(target)
        RETURN r
        """

        with self.driver.session() as session:
            session.run(
                query,
                source_id=source_scene_id,
                target_id=target_scene_id,
                choice_text=choice_text
            )

    # ── Fix #5: Fire-and-forget wrapper for non-blocking DB writes ──

    def save_scene_async(self, narrative: str, available_choices: list[str],
                         story_title: str, source_scene_id: str | None,
                         choice_text: str | None, on_complete=None):
        """
        Writes a new scene node (and optional edge from previous scene) to Neo4j
        in a background daemon thread so it doesn't block the UI.
        Calls on_complete(new_scene_id) when done.
        """
        def _write():
            new_scene_id = self.create_scene_node(narrative, available_choices, story_title)
            if source_scene_id and choice_text:
                self.create_choice_edge(source_scene_id, new_scene_id, choice_text)
            if on_complete:
                on_complete(new_scene_id)

        t = threading.Thread(target=_write, daemon=True)
        t.start()

    def get_scene_history_path(self, current_scene_id: str):
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
            record = result.first()
            if not record:
                return None

            scenes = [{"id": n["id"], "narrative": n["narrative"]} for n in record["scenes"]]
            choices = [r["action_text"] for r in record["choices"]]

            return {"scenes": scenes, "choices": choices}


# Example Usage
if __name__ == "__main__":
    db = CYOAGraphDB()
    try:
        story_title = db.create_story_node_and_get_title("The Dark Forest Escape")
        scene1 = db.create_scene_node("You wake up in a dark forest.", ["Walk north."], story_title)
        scene2 = db.create_scene_node("You find an abandoned cabin.", [], story_title)
        db.create_choice_edge(scene1, scene2, "Walk north.")
        print(f"Graph initialized with nodes {scene1} and {scene2}")
        print(db.get_scene_history_path(scene2))
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()
