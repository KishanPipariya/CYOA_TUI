import json
import os
from llama_cpp import Llama
from models import StoryNode

# Fix #4: Max number of (assistant, user) turn pairs to keep in context.
# Older turns beyond this are trimmed to prevent context window overflow.
MAX_CONTEXT_TURNS = 10


class StoryContext:
    def __init__(self, starting_prompt: str, max_turns: int = MAX_CONTEXT_TURNS):
        self.max_turns = max_turns
        self.history = [
            {"role": "system", "content": (
                "You are a creative interactive fiction engine. The user makes choices, "
                "and you narrate the consequences and provide the next set of choices. "
                "Keep the narrative engaging and concise (1-2 paragraphs max). "
                "Always respond in JSON matching the requested schema."
            )}
        ]
        self.starting_prompt = starting_prompt
        self.history.append({"role": "user", "content": starting_prompt})

    def add_turn(self, raw_narrative: str, user_choice: str):
        """
        Add an assistant turn (raw narrative text only) and user choice.

        Fix #2: We now accept and store only the raw narrative string, NOT the
        accumulated rendered Markdown (_current_story). This keeps each turn
        concise and prevents the LLM from re-reading its entire output history.
        """
        self.history.append({"role": "assistant", "content": raw_narrative})
        self.history.append({"role": "user", "content": f"I choose: {user_choice}"})

        # Fix #4: Sliding window — trim oldest turns if context is too large.
        # Always preserve index 0 (system) and index 1 (initial user prompt).
        # Each turn is 2 messages (assistant + user), so trim from index 2.
        non_system_messages = self.history[2:]  # exclude system + initial prompt
        if len(non_system_messages) > self.max_turns * 2:
            # Drop the oldest (assistant, user) pair
            self.history = self.history[:2] + non_system_messages[2:]


class StoryGenerator:
    def __init__(self, model_path: str, n_ctx: int = 4096):
        # Perf #4: Use physical core count for CPU threads — avoids over-scheduling
        # on Apple Silicon where efficiency cores compete with the Metal GPU queue.
        cpu_threads = max(1, (os.cpu_count() or 8) // 2)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=cpu_threads,
            n_gpu_layers=-1,
            flash_attn=True,
            verbose=False
        )
        # Perf #1: Cache schema once at init — model_json_schema() is not cheap
        self._schema = StoryNode.model_json_schema()

    def generate_next_node(self, context: StoryContext) -> StoryNode:
        """
        Generates the next story node given the current context history.
        Uses structured JSON schema via llama.cpp constrained outputs.
        """
        response = self.llm.create_chat_completion(
            messages=context.history,
            response_format={
                "type": "json_object",
                "schema": self._schema,  # Perf #1: use cached schema
            },
            temperature=0.6,  # Perf #5: slightly lower temp speeds up grammar-constrained sampling
            max_tokens=512,
        )

        content = response["choices"][0]["message"]["content"]

        try:
            data = json.loads(content)
            return StoryNode(**data)
        except Exception as e:
            print(f"Failed to parse LLM output: {e}\nOutput was: {content}")
            return StoryNode(
                narrative=(
                    "The universe encounters an anomaly (LLM failed to format its response). "
                    "You find yourself back where you started."
                ),
                choices=[{"text": "Try doing something different."}]
            )
