import os
import json
import pyperclip
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Header, Footer, Label, ListView, ListItem, Static
from textual.binding import Binding

class JsonFileSelector(App):
    """TUI for selecting files based on a JSON list from the clipboard."""
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #main-container { padding: 1 2; }
    .title { background: #4a3f39; color: #d08c60; padding: 1; text-style: bold; text-align: center; margin-bottom: 1; }
    ListView { border: solid #5a4d45; background: #241f1c; height: 1fr; }
    ListItem { height: auto; }
    """
    BINDINGS = [
        Binding("q", "confirm", "Confirm List"),
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "reload", "Reload Clipboard")
    ]

    def __init__(self, root_dir: str):
        super().__init__()
        self.root_dir = root_dir
        self.files = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-container"):
            yield Label("Waiting for JSON file list on clipboard...", id="status-title", classes="title")
            yield ListView(id="file-list")
            yield Static("\n[bold]Instructions:[/bold] Copy a JSON array of strings (e.g. [\"file1.py\", \"file2.py\"]) to proceed.")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.5, self.check_clipboard)

    def action_reload(self) -> None:
        self.check_clipboard(force=True)

    def check_clipboard(self, force=False) -> None:
        try:
            content = pyperclip.paste().strip()
            if not content:
                return
            if content.startswith("["):
                data = json.loads(content)
                if isinstance(data, list) and data != self.files:
                    self.files = data
                    self.update_list(data)
        except Exception:
            pass

    def update_list(self, files: list[str]) -> None:
        list_view = self.query_one("#file-list", ListView)
        list_view.clear()
        self.query_one("#status-title", Label).update("[bold green]JSON File List Detected[/bold green]")
        for f in files:
            list_view.append(ListItem(Label(f)))

    def action_confirm(self) -> None:
        if self.files:
            abs_files = [os.path.abspath(os.path.join(self.root_dir, f)) for f in self.files]
            self.exit(abs_files)

    def action_cancel(self) -> None:
        self.exit(None)
