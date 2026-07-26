import os
import shutil
import subprocess
import tempfile
import threading
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Label, ListView, ListItem, Input, TextArea, Button, Static
from textual.screen import Screen
from textual.binding import Binding

from combinecopy.utils import load_default_rules, save_default_rule, safe_read_file

class RulesScreen(Screen[str | None]):
    """Full-screen TUI for browsing, editing, and creating rules."""
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #rules-layout { height: 100%; }
    #rules-left {
        width: 32%;
        border-right: solid #5a4d45;
        background: #241f1c;
        padding: 1;
    }
    #rules-right {
        width: 68%;
        padding: 1 2;
    }
    .panel-title {
        background: #4a3f39;
        color: #d08c60;
        padding: 1;
        text-style: bold; 
        margin-bottom: 1;
    }
    ListView {
        border: solid #5a4d45;
        background: #1e1a18;
        height: 1fr;
        margin-bottom: 1;
    }
    Input {
        border: solid #5a4d45;
        background: #1e1a18;
        margin-bottom: 1;
    }
    TextArea {
        border: solid #5a4d45;
        background: #1e1a18;
        height: 1fr;
        margin-bottom: 1;
    }
    TextArea:focus, Input:focus {
        border: double #d08c60;
    }
    #rule-action-buttons {
        height: 3;
        margin-top: 1;
        align: right middle;
    }
    Button {
        margin-left: 1;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Back / Cancel"),
        Binding("f2", "open_editor", "Open in Editor"),
    ]

    def __init__(self, root_dir: str):
        super().__init__()
        self.root_dir = root_dir
        self.default_rules = load_default_rules(self.root_dir)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="rules-layout"):
            with Vertical(id="rules-left"):
                yield Label("Rule Catalog", classes="panel-title")
                items = [
                    ListItem(Label("➕ Create New Rule from Scratch"), id="rule-scratch")
                ]
                ccrules_path = os.path.join(self.root_dir, ".ccrules")
                if os.path.exists(ccrules_path):
                    items.append(ListItem(Label("📄 Existing Project Rules (.ccrules)"), id="rule-ccrules"))
                
                for idx, r in enumerate(self.default_rules):
                    items.append(ListItem(Label(f"📚 {r.get('title', f'Rule {idx+1}')}"), id=f"rule-def-{idx}"))
                    
                yield ListView(*items, id="rule-list")
                yield Static("Select a rule from the list to view, edit, or copy its directives.", classes="dim")

            with Vertical(id="rules-right"):
                yield Label("Rule Title:", classes="panel-title")
                yield Input(placeholder="e.g. Orwell Coding", id="rule-title-input")
                yield Label("Rule Directives / Instructions:", classes="panel-title")
                yield TextArea(id="rule-content-textarea")
                with Horizontal(id="rule-action-buttons"):
                    yield Button("Save & Apply to .ccrules", id="btn-save-workspace", variant="success")
                    yield Button("Save to Default Rules", id="btn-save-default", variant="primary")
                    yield Button("Open in Notepad++ (F2)", id="btn-editor", variant="warning")
                    yield Button("Back to Prompt", id="btn-cancel", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        rule_list = self.query_one("#rule-list", ListView)
        ccrules_path = os.path.join(self.root_dir, ".ccrules")
        if os.path.exists(ccrules_path):
            rule_list.index = 1
            self._load_rule_by_id("rule-ccrules")
        elif len(self.default_rules) > 0:
            rule_list.index = 1
            self._load_rule_by_id("rule-def-0")
        else:
            rule_list.index = 0
            self._load_rule_by_id("rule-scratch")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and event.item.id:
            self._load_rule_by_id(event.item.id)

    def _load_rule_by_id(self, item_id: str) -> None:
        title_input = self.query_one("#rule-title-input", Input)
        content_ta = self.query_one("#rule-content-textarea", TextArea)

        if item_id == "rule-scratch":
            title_input.value = "Custom Rule"
            content_ta.text = "# Define your custom rules here\n"
        elif item_id == "rule-ccrules":
            ccrules_path = os.path.join(self.root_dir, ".ccrules")
            title_input.value = "Project Rule (.ccrules)"
            content_ta.text = safe_read_file(ccrules_path)
        elif item_id.startswith("rule-def-"):
            try:
                idx = int(item_id.replace("rule-def-", ""))
                if 0 <= idx < len(self.default_rules):
                    rule = self.default_rules[idx]
                    title_input.value = rule.get("title", "")
                    content_ta.text = rule.get("content", "")
            except ValueError:
                pass

    def action_open_editor(self) -> None:
        btn = self.query_one("#btn-editor", Button)
        if btn.disabled:
            return
        btn.disabled = True

        current_text = self.query_one("#rule-content-textarea", TextArea).text
        thread = threading.Thread(target=self._editor_worker, args=(current_text,), daemon=True)
        thread.start()
        self.notify("Waiting for external editor to close...", severity="info")

    def _editor_worker(self, current_text: str) -> None:
        fd, temp_path = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(current_text)

        npp_path = shutil.which("notepad++") or shutil.which("notepad++.exe")
        if not npp_path:
            possible_paths = [
                r"C:\Program Files\Notepad++\notepad++.exe",
                r"C:\Program Files (x86)\Notepad++\notepad++.exe"
            ]
            for p in possible_paths:
                if os.path.exists(p):
                    npp_path = p
                    break

        if npp_path:
            cmd = [npp_path, "-multiInst", "-nosession", temp_path]
        elif os.name == 'nt':
            cmd = ["notepad", temp_path]
        else:
            editor = os.environ.get('EDITOR', 'nano')
            cmd = [editor, temp_path]

        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            self.call_from_thread(self.notify, f"Editor failed to launch: {e}", severity="error")

        try:
            with open(temp_path, 'r', encoding='utf-8') as f:
                new_text = f.read()
            self.call_from_thread(self._update_content_text, new_text)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to read from editor: {e}", severity="error")
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            self.call_from_thread(self._enable_editor_button)

    def _update_content_text(self, new_text: str) -> None:
        ta = self.query_one("#rule-content-textarea", TextArea)
        ta.text = new_text
        self.notify("Rule text updated from editor!", title="Success")

    def _enable_editor_button(self) -> None:
        self.query_one("#btn-editor", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-save-workspace":
            self.action_save_workspace()
        elif btn_id == "btn-save-default":
            self.action_save_default()
        elif btn_id == "btn-editor":
            self.action_open_editor()
        elif btn_id == "btn-cancel":
            self.action_cancel()

    def action_save_workspace(self) -> None:
        content = self.query_one("#rule-content-textarea", TextArea).text.strip()
        ccrules_path = os.path.join(self.root_dir, ".ccrules")
        try:
            with open(ccrules_path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            self.notify("Saved rule to .ccrules!", title="Success")
            self.dismiss(content)
        except Exception as e:
            self.notify(f"Failed to write .ccrules: {e}", severity="error")

    def action_save_default(self) -> None:
        title = self.query_one("#rule-title-input", Input).value.strip()
        content = self.query_one("#rule-content-textarea", TextArea).text.strip()

        if not title:
            self.notify("Please provide a Title before saving as a Default Rule.", severity="error")
            return

        if save_default_rule(title, content, root_dir=self.root_dir):
            self.notify(f"Saved '{title}' to default_rules.json!", title="Success")
            self.default_rules = load_default_rules(self.root_dir)
            
            rule_list = self.query_one("#rule-list", ListView)
            rule_list.clear()
            rule_list.append(ListItem(Label("➕ Create New Rule from Scratch"), id="rule-scratch"))
            
            ccrules_path = os.path.join(self.root_dir, ".ccrules")
            if os.path.exists(ccrules_path):
                rule_list.append(ListItem(Label("📄 Existing Project Rules (.ccrules)"), id="rule-ccrules"))

            target_idx = -1
            for idx, r in enumerate(self.default_rules):
                item_id = f"rule-def-{idx}"
                rule_list.append(ListItem(Label(f"📚 {r.get('title', f'Rule {idx+1}')}"), id=item_id))
                if r.get("title", "").strip().lower() == title.lower():
                    target_idx = len(rule_list) - 1

            if target_idx != -1:
                rule_list.index = target_idx
        else:
            self.notify("Failed to save to default_rules.json.", severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)
