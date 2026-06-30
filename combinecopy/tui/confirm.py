from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, Button
from textual.binding import Binding

class ConfirmCopyApp(App):
    """TUI for confirming large copy operations."""
    CSS = """
    Screen {
        align: center middle;
        background: #2d2825;
    }
    #dialog {
        padding: 1 2;
        border: solid #d08c60;
        background: #3c3431;
        width: auto;
        height: auto;
    }
    .title {
        text-align: center;
        text-style: bold;
        color: #ead6c9;
        margin-bottom: 1;
    }
    .buttons {
        align: center middle;
        height: 3;
        margin-top: 1;
    }
    Button {
        margin: 0 1;
    }
    """
    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, file_count: int):
        super().__init__()
        self.file_count = file_count

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"About to copy {self.file_count} files. Are you sure?", classes="title")
            with Horizontal(classes="buttons"):
                yield Button("Yes (y)", id="btn-yes", variant="success")
                yield Button("No (n)", id="btn-no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.action_confirm()
        elif event.button.id == "btn-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.exit(True)

    def action_cancel(self) -> None:
        self.exit(False)
