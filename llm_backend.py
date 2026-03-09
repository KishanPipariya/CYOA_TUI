import json
from llama_cpp import Llama
from models import StoryNode

class StoryContext:
    def __init__(self, starting_prompt: str):
        self.history = [
            {"role": "system", "content": "You are a creative interactive fiction engine. The user makes choices, and you narrate the consequences and provide the next set of choices. Keep the narrative engaging and concise (1-2 paragraphs max). Always respond in JSON matching the requested schema."}
        ]
        self.starting_prompt = starting_prompt
        # Add the start prompt to history
        self.history.append({"role": "user", "content": starting_prompt})
    
    def add_turn(self, narrative: str, user_choice: str):
        self.history.append({"role": "assistant", "content": narrative})
        self.history.append({"role": "user", "content": f"I choose: {user_choice}"})

class StoryGenerator:
    def __init__(self, model_path: str, n_ctx: int = 4096):
        # Initialize the Llama model
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=-1, # Try to offload to GPU if available (e.g. Metal on Mac)
            verbose=False
        )
    
    def generate_next_node(self, context: StoryContext) -> StoryNode:
        """
        Generates the next story node given the current context history.
        Uses structured grammar/json schema via llama.cpp structured outputs if possible,
        or just asks for JSON.
        """
        
        # Define the schema we expect
        schema = StoryNode.model_json_schema()
        
        # Use llama-cpp-python's response format feature for JSON schema
        response = self.llm.create_chat_completion(
            messages=context.history,
            response_format={
                "type": "json_object",
                "schema": schema,
            },
            temperature=0.7,
            max_tokens=512,
        )
        
        content = response["choices"][0]["message"]["content"]
        
        try:
            # Parse the JSON string into our Pydantic model
            data = json.loads(content)
            return StoryNode(**data)
        except Exception as e:
            # Fallback or error handling
            print(f"Failed to parse LLM output: {e}\nOutput was: {content}")
            return StoryNode(
                narrative="The universe encounters an anomaly (LLM failed to format its response correctly). You find yourself back where you started.",
                choices=[{"text": "Try doing something else instead."}]
            )
