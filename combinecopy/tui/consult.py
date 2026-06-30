from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Button, ListView, ListItem, Static
from textual.screen import ModalScreen
from combinecopy.prompts import build_external_consult_prompt
from combinecopy.utils import copy_to_clipboard

class ConsultationScreen(ModalScreen[bool]):
    """TUI for verifying and copying external AI consultation queries (Air-gapped Mode)."""
    CSS = """
    ConsultationScreen { 
        align: center middle; 
        background: rgba(0, 0, 0, 0.8); 
    }
    #consult-dialog { 
        width: 90%; 
        height: 90%; 
        border: solid #d08c60; 
        background: #2d2825; 
        padding: 1 2; 
    }
    .consult-title { 
        text-align: center; 
        text-style: bold; 
        color: #d08c60; 
        margin-bottom: 1; 
    }
    #consult-body { height: 1fr; }
    #consult-left { width: 50%; border-right: solid #5a4d45; padding-right: 1; }
    #consult-right { width: 50%; padding-left: 1; }
    #consult-footer { height: 3; align: right middle; margin-top: 1; border-top: solid #5a4d45; }
    Button { margin-left: 1; }
    .query-card { 
        border: solid #5a4d45; 
        background: #1e1a18; 
        padding: 1; 
        margin-bottom: 1; 
        height: auto;
    }
    """
    
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.queries = data.get("queries", [])
        
    def compose(self) -> ComposeResult:
        with Vertical(id="consult-dialog"):
            yield Label("Consult Phase: External Air-Gapped Request", classes="consult-title")
            with Horizontal(id="consult-body"):
                with Vertical(id="consult-left"):
                    yield Label("[bold yellow]DLP Review: Verify Queries[/bold yellow]")
                    yield Static("Ensure no proprietary IP or company code is leaked in these questions before proceeding.", classes="dim")
                    lv = ListView()
                    for q in self.queries:
                        lv.append(ListItem(Static(
                            f"[bold cyan]ID: {q.get('id', 'N/A')}[/bold cyan]\n"
                            f"{q.get('question', '')}"
                            , classes="query-card")))
                    yield lv
                with Vertical(id="consult-right"):
                    yield Label("[bold green]Instructions[/bold green]")
                    yield Static(
                        "1. Click [bold]Verify & Copy Prompt[/bold] below.\n"
                        "2. Paste the prompt into your external LLM (ChatGPT/Claude).\n"
                        "3. Wait for the external AI to generate the XML answers.\n"
                        "4. Copy the external AI's response to your clipboard.\n\n"
                        "[dim]This screen will automatically close when it detects valid <consultation_results> on your clipboard.[/dim]"
                    )
            with Horizontal(id="consult-footer"):
                yield Button("Verify & Copy Prompt", id="btn-copy", variant="success")
                yield Button("Cancel Consult", id="btn-cancel", variant="error")
                
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy":
            prompt = build_external_consult_prompt(self.queries)
            if copy_to_clipboard(prompt):
                self.app.notify("External prompt copied! Awaiting response...", title="Copied")
        elif event.button.id == "btn-cancel":
            self.dismiss(True)
