from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Label, Button, Select, Static
from textual.binding import Binding

class ResolutionApp(App):
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #main-container { padding: 1 2; overflow-y: auto; }
    .title { background: #4a3f39; color: #d08c60; padding: 1; text-style: bold; text-align: center; margin-bottom: 1; }
    .req-card { border: solid #5a4d45; background: #241f1c; padding: 1; margin-bottom: 1; }
    .req-label { text-style: bold; color: #ead6c9; margin-bottom: 1; }
    Select { margin-bottom: 1; }
    #btn-row { height: 3; margin-top: 1; align: center middle; }
    Button { margin: 0 1; }
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, ambiguous: dict, missing: list):
        super().__init__()
        self.ambiguous = ambiguous
        self.missing = missing
        self.results = {}
        self.select_map = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-container"):
            yield Label("Resolve AI File Requests", classes="title")
            yield Static("[yellow]Some requested files were ambiguous or not found. Please resolve them below.[/yellow]\n")
            
            for i, (req, options) in enumerate(self.ambiguous.items()):
                with Vertical(classes="req-card"):
                    yield Label(f"Ambiguous: {req}", classes="req-label")
                    sel_options = [(opt, opt) for opt in options]
                    sel_options.insert(0, ("Skip this file", ""))
                    w_id = f"sel_{i}"
                    self.select_map[w_id] = req
                    yield Select(sel_options, id=w_id, value=options[0])
                    
            for req in self.missing:
                with Vertical(classes="req-card"):
                    yield Label(f"Not Found: {req}", classes="req-label")
                    yield Static("[dim]This file does not exist in the workspace and will be skipped.[/dim]")
                    
            with Horizontal(id="btn-row"):
                yield Button("Confirm Resolutions", id="btn-confirm", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="error")
        yield Footer()
        
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            for w_id, req in self.select_map.items():
                try:
                    sel = self.query_one(f"#{w_id}", Select)
                    if sel.value:
                        self.results[req] = str(sel.value)
                except Exception:
                    pass
            self.exit(self.results)
        elif event.button.id == "btn-cancel":
            self.exit(None)

    def action_cancel(self) -> None:
        self.exit(None)
