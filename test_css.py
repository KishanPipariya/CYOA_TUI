from textual.app import App

class DummyApp(App):
    CSS_PATH = "cyoa/ui/styles.tcss"

if __name__ == "__main__":
    try:
        app = DummyApp()
        app.run(headless=True)
    except Exception as e:
        print(f"Exception: {type(e)}")
        print(e)
        if hasattr(e, 'errors'):
            print(e.errors)
