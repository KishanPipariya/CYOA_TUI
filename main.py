import argparse
import sys
from app import CYOAApp

def main():
    parser = argparse.ArgumentParser(description="CYOA Terminal Game with Local LLM")
    parser.add_argument("--model", type=str, required=True, help="Path to the .gguf model file")
    
    args = parser.parse_args()
    
    # Run the textual app
    app = CYOAApp(model_path=args.model)
    app.run()

if __name__ == "__main__":
    main()
