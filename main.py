import argparse
from app import CYOAApp, DEFAULT_STARTING_PROMPT

def main():
    parser = argparse.ArgumentParser(description="CYOA Terminal Game with Local LLM")
    parser.add_argument("--model", type=str, required=True, help="Path to the .gguf model file")
    parser.add_argument(
        "--prompt", type=str, default=None,
        help="Optional custom starting prompt/scenario. Uses the default dungeon scenario if not provided."
    )

    args = parser.parse_args()

    starting_prompt = args.prompt if args.prompt else DEFAULT_STARTING_PROMPT
    app = CYOAApp(model_path=args.model, starting_prompt=starting_prompt)
    app.run()

if __name__ == "__main__":
    main()
