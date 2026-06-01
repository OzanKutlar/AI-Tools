import os
import threading
import tempfile
import shutil
import subprocess
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Label, TextArea, Button, OptionList
from textual.binding import Binding

class SystemPromptApp(App):
    """TUI for injecting system instructions and user requests."""
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #layout { height: 100%; }
    #left-pane {
        width: 30%;
        border-right: solid #5a4d45;
        background: #241f1c;
    }
    #right-pane {
        width: 70%;
        padding: 1 2;
    }
    .panel-title {
        background: #4a3f39;
        color: #d08c60;
        padding: 1;
        text-style: bold; 
        margin-bottom: 1;
    }
    TextArea {
        border: solid #5a4d45;
        background: #1e1a18;
        margin-bottom: 1;
    }
    #left-pane OptionList { height: 1fr; }
    TextArea:focus {
        border: double #d08c60;
    }
    #user-request { height: 1fr; }
    #sys-prompt { height: 2fr; }
    #action-buttons { height: 3; margin-top: 1; margin-bottom: 1; }
    #btn-submit { width: 1fr; margin-right: 1; }
    #btn-editor { width: auto; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+j", "submit", "Submit Request", show=False),
        Binding("ctrl+enter", "submit", "Submit Request"),
        Binding("f2", "open_editor", "Open in Editor")
    ]
    
    def __init__(self, root_dir: str, files: list[str], sys_prompt: str):
        super().__init__()
        self.root_dir = root_dir
        self.files = files
        self.sys_prompt = sys_prompt
        
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="left-pane"):
                yield Label("Files in Context", classes="panel-title")
                rel_files = [os.path.relpath(f, self.root_dir) for f in self.files]
                yield OptionList(*rel_files)
            with Vertical(id="right-pane"):
                yield Label("Your Request / Problem (Ctrl+Enter to Submit):", classes="panel-title")
                yield TextArea(id="user-request", text="")
                yield Label("System Prompt (Injected):", classes="panel-title")
                yield TextArea(id="sys-prompt", text=self.sys_prompt)
                with Horizontal(id="action-buttons"):
                    yield Button("Submit & Continue (Ctrl+Enter)", id="btn-submit", variant="success")
                    yield Button("Open in Notepad++ (F2)", id="btn-editor", variant="primary")
        yield Footer()
        
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self.action_submit()
        elif event.button.id == "btn-editor":
            self.action_open_editor()
            
    def action_open_editor(self) -> None:
        btn = self.query_one("#btn-editor", Button)
        if btn.disabled:
            return
        btn.disabled = True
        
        current_text = self.query_one("#user-request", TextArea).text
        thread = threading.Thread(target=self._editor_worker, args=(current_text,), daemon=True)
        thread.start()
        self.notify("Waiting for external editor to close...", severity="info")
        
    def _editor_worker(self, current_text: str) -> None:
        fd, temp_path = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
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
            self.call_from_thread(self._update_request_text, new_text)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to read from editor: {e}", severity="error")
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            self.call_from_thread(self._enable_editor_button)
            
    def _update_request_text(self, new_text: str) -> None:
        ta = self.query_one("#user-request", TextArea)
        ta.text = new_text
        self.notify("Text updated from editor!", title="Success")
        
    def _enable_editor_button(self) -> None:
        self.query_one("#btn-editor", Button).disabled = False

    def action_submit(self) -> None:
        req = self.query_one("#user-request", TextArea).text
        sys_text = self.query_one("#sys-prompt", TextArea).text
        self.exit({"request": req, "system": sys_text})
        
    def action_cancel(self) -> None:
        self.exit(None)
