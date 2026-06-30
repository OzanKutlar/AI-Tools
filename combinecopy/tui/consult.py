from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Button, ListView, ListItem, Static, Markdown
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
        #consult-left { width: 40%; border-right: solid #5a4d45; padding-right: 1; }
        #consult-right { width: 60%; padding-left: 1; }
        #consult-footer { height: 3; align: right middle; margin-top: 1; border-top: solid #5a4d45; }
        Button { margin-left: 1; }
        .query-card { 
            border: solid #5a4d45; 
            background: #1e1a18; 
            padding: 1; 
            margin-bottom: 1; 
            height: auto;
        }
        #query-detail {
            height: 1fr;
            overflow-y: auto;
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
                        list_items = []
                        for q in self.queries:
                            q_id = q.get('id', 'N/A')
                            q_text = q.get('question', '')
                            snippet = q_text[:60] + "..." if len(q_text) > 60 else q_text
                            list_items.append(ListItem(Static(
                                f"[bold cyan]ID: {q_id}[/bold cyan]\n{snippet}",
                                classes="query-card"
                            ), id=f"query-{q_id}"))
                        yield ListView(*list_items, id="query-list")
                    with Vertical(id="consult-right"):
                        yield Markdown(self._get_instructions(), id="query-detail")
                with Horizontal(id="consult-footer"):
                    yield Button("Verify & Copy Prompt", id="btn-copy", variant="success")
                    yield Button("Cancel Consult", id="btn-cancel", variant="error")

        def _get_instructions(self) -> str:
            return (
                "### Instructions\n"
                "1. Click **Verify & Copy Prompt** below.\n"
                "2. Paste the prompt into your external LLM (ChatGPT/Claude).\n"
                "3. Wait for the external AI to generate the XML answers.\n"
                "4. Copy the external AI's response to your clipboard.\n\n"
                "*This screen will automatically close when it detects valid `<consultation_results>` on your clipboard.*\n\n"
                "---\n"
                "**Select a query on the left to view its full contents here.**"
            )

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            if event.item and event.item.id and event.item.id.startswith("query-"):
                q_id = event.item.id.replace("query-", "")
                q_obj = next((q for q in self.queries if q.get("id") == q_id), None)
                if q_obj:
                    md = self.query_one("#query-detail", Markdown)
                    md.update(f"### Query ID: {q_id}\n\n{q_obj.get('question', '')}")

        def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy":
            prompt = build_external_consult_prompt(self.queries)
            if copy_to_clipboard(prompt):
                self.app.notify("External prompt copied! Awaiting response...", title="Copied")
        elif event.button.id == "btn-cancel":
            self.dismiss(True)
