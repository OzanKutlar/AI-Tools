import os
import sys
import time
import argparse
import subprocess
import json
import hashlib

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Input, Label, Button, RichLog, Static, ProgressBar, OptionList
from textual.reactive import reactive

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

try:
    import pyperclip
    PYCLIPBOARD_AVAILABLE = True
except ImportError:
    PYCLIPBOARD_AVAILABLE = False

from cc_utils import safe_read_file

# --- Git Utilities (Reused and adapted from ftpapp) ---

def get_changed_files(commit_hash: str) -> list[dict]:
    """Extracts Added, Modified, and Deleted files using git diff against HEAD."""
    try:
        out = subprocess.check_output(
            ['git', 'diff', '--no-renames', '--name-status', '--diff-filter=AMD', commit_hash, 'HEAD'],
            text=True,
            stderr=subprocess.STDOUT
        )
        files = []
        for line in out.strip().split('\n'):
            if not line: continue
            parts = line.split('\t')
            if len(parts) >= 2:
                status = parts[0]
                filepath = parts[-1]
                
                if status == 'D':
                    files.append({"path": filepath, "action": "delete"})
                elif status in ('A', 'M'):
                    if os.path.exists(filepath):
                        files.append({"path": filepath, "action": "apply"})
        return files
    except subprocess.CalledProcessError as e:
        print(f"Git error resolving commit {commit_hash}: {e.output}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: Git is not installed or not in PATH.")
        sys.exit(1)


def get_git_commits(limit: int = 50) -> list[dict]:
    """Retrieves the last N commits from git log."""
    try:
        out = subprocess.check_output(
            ['git', 'log', f'-n', str(limit), '--pretty=format:%h|%an|%ar|%s'],
            text=True,
            stderr=subprocess.STDOUT
        )
        commits = []
        for line in out.strip().split('\n'):
            if not line:
                continue
            parts = line.split('|', 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3]
                })
        return commits
    except Exception:
        return []


def get_config_filepath() -> str:
    """Returns the path to the config file for the current repository folder."""
    dir_path = os.path.expanduser("~/.configs/webapp")
    try:
        os.makedirs(dir_path, exist_ok=True)
    except Exception:
        pass
    cwd = os.path.abspath(os.getcwd())
    cwd_hash = hashlib.md5(cwd.encode('utf-8')).hexdigest()
    return os.path.join(dir_path, f"config_{cwd_hash}.json")


def load_saved_config() -> dict:
    """Loads configuration from JSON file if it exists."""
    try:
        path = get_config_filepath()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def save_config(commit: str, hotkey: str) -> None:
    """Saves folder configuration."""
    try:
        path = get_config_filepath()
        data = {
            "commit": commit,
            "hotkey": hotkey
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

# --- Textual Components ---

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

class FileItem(Static):
    """Represents a single file in the queue with a reactive status."""
    
    status = reactive("pending") # pending, active, done, skipped, error
    
    def __init__(self, filepath: str, action: str = "apply", **kwargs):
        super().__init__(**kwargs)
        self.filepath = filepath
        self.action = action
        self.spinner_idx = 0
        self.spinner_timer = None

    def on_mount(self) -> None:
        self.spinner_timer = self.set_interval(0.1, self.tick_spinner, pause=True)

    def tick_spinner(self) -> None:
        if self.status == "active":
            self.spinner_idx = (self.spinner_idx + 1) % len(SPINNER_FRAMES)
            self.refresh()

    def watch_status(self, old_status: str, new_status: str) -> None:
        if new_status == "active":
            self.spinner_timer.resume()
        else: 
            if self.spinner_timer:
                self.spinner_timer.pause()
        self.refresh()

    def render(self) -> str:
        if self.status == "pending":
            icon = "[dim]○[/dim]" if self.action == "apply" else "[dim]🗑[/dim]"
            style = "dim"
        elif self.status == "active":
            icon = f"[bold yellow]{SPINNER_FRAMES[self.spinner_idx]}[/bold yellow]"
            style = "bold yellow"
        elif self.status == "done":
            icon = "[bold green]✓[/bold green]"
            style = "green"
        elif self.status == "skipped":
            icon = "[dim]↷[/dim]"
            style = "dim strike"
        else:
            icon = "[bold red]✗[/bold red]"
            style = "red"
            
        action_prefix = "[bold red]\\[DEL\\][/bold red] " if self.action == "delete" else ""
        return f"{icon} {action_prefix}[{style}]{self.filepath}[/{style}]"


class SetupApp(App):
    """TUI Form to select commit and hotkey parameters."""

    CSS = """
    Screen { align: center middle; background: #2d2825; }
    #setup-container {
        width: 60%;
        height: auto;
        border: solid #d08c60; 
        background: #3c3431;
        padding: 1 2;
    }
    Label { margin-top: 1; color: #ead6c9; }
    Input { background: #1e1a18; border: tall #5a4d45; }
    Input:focus { border: tall #d08c60; }
    OptionList { height: 8; background: #1e1a18; border: tall #5a4d45; margin-top: 1; }
    OptionList:focus { border: tall #d08c60; }
    Button { margin-top: 2; width: 100%; }
    .title { text-align: center; text-style: bold; color: #d08c60; margin-bottom: 1; }
    #lbl-commit-details {
        background: #241f1c;
        padding: 1;
        border-left: solid #d08c60;
        margin-top: 1;
        margin-bottom: 1;
        height: 3;
    }
    """

    def __init__(self, initial_args: dict):
        super().__init__()
        self.initial_args = initial_args
        self.result = None
        self.commits = get_git_commits()

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-container"):
            yield Label("Web Apply Configuration", classes="title")
            
            yield Label("Macro Trigger Hotkey (e.g., +, ctrl+alt+v, insert)")
            yield Input(value=self.initial_args.get("hotkey", "+"), id="inp-hotkey")
            
            if self.commits:
                yield Label("Select Commit to Diff Against HEAD (Use Arrows & Enter)")
                yield Label("", id="lbl-commit-details")
                yield OptionList(
                    *[f"{c['hash']} - {c['subject']}" for c in self.commits],
                    id="inp-commit-list"
                ) 
            else:
                yield Label("Baseline Commit Hash (e.g. HEAD~1, a1b2c3d)")
                yield Input(value=self.initial_args.get("commit", "HEAD~1"), id="inp-commit")
            
            yield Button("Start Apply Session", variant="success", id="btn-start")

    def on_mount(self) -> None:
        if self.commits:
            self.update_commit_display(0)

    def update_commit_display(self, index: int) -> None:
        if 0 <= index < len(self.commits):
            commit = self.commits[index]
            text = f"Selected: [bold cyan]{commit['hash']}[/bold cyan] ({commit['date']}) by {commit['author']}\n[dim]\"{commit['subject']}\"[/dim]"
            self.query_one("#lbl-commit-details", Label).update(text)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.update_commit_display(event.option_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            if self.commits:
                list_widget = self.query_one("#inp-commit-list", OptionList)
                idx = list_widget.highlighted
                commit_val = self.commits[idx]["hash"] if idx is not None and 0 <= idx < len(self.commits) else ""
            else:
                commit_val = self.query_one("#inp-commit", Input).value.strip()

            self.result = {
                "commit": commit_val,
                "hotkey": self.query_one("#inp-hotkey", Input).value.strip()
            }
            if not self.result["commit"] or not self.result["hotkey"]:
                self.notify("Please fill in all fields.", severity="error")
                return
            self.exit(self.result)


class WebApp(App):
    """Main Web Apply App with global keyboard macro listening."""

    CSS = """
    Screen { background: #2d2825; }
    #layout { height: 100%; }
    
    #left-panel {
        width: 35%;
        border-right: solid #5a4d45;
        height: 100%;
        background: #241f1c;
    }
    
    #file-list-container { padding: 1 2; }
    
    #right-panel {
        width: 65%;
        height: 100%;
    }
    
    #stats-panel {
        height: 40%;
        border-bottom: solid #5a4d45;
        padding: 1 2;
    }
    
    #preview-panel {
        height: 60%;
    }
    
    #preview-log { height: 1fr; background: #1e1a18; }
    
    .panel-title {
        background: #4a3f39;
        color: #d08c60;
        padding: 1;
        text-style: bold;
        margin-bottom: 1;
        text-align: center;
    }
    
    #global-progress { margin-bottom: 1; }
    .stat-label { margin-bottom: 1; color: #ead6c9; }
    .control-hint { color: #d08c60; text-style: italic; }
    """

    def __init__(self, config: dict, files: list[dict]):
        super().__init__()
        self.config = config
        self.files = files
        self.active_index = 0
        self.is_applying = False
        self.hotkey_hook = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="layout"):
            # Left Panel: File list
            with Vertical(id="left-panel"):
                yield Label("Files Queue", classes="panel-title")
                with ScrollableContainer(id="file-list-container"):
                    for i, file_info in enumerate(self.files):
                        yield FileItem(file_info["path"], action=file_info["action"], id=f"file-{i}")
                        
            # Right Panel: Active Status, Control Hints, Preview Log
            with Vertical(id="right-panel"):
                with Vertical(id="stats-panel"):
                    yield Label("Session Progress", classes="panel-title")
                    yield ProgressBar(total=len(self.files), show_eta=False, id="global-progress")
                    yield Label("Active Hotkey: " + self.config["hotkey"], classes="stat-label")
                    yield Label("Current File: Preparing...", id="lbl-current", classes="stat-label")
                    yield Label("Instructions:\n1. Go to your browser or web editor.\n2. Click inside the active editor pane.\n3. Press [bold]" + self.config["hotkey"] + "[/bold] to auto-replace content.\n\n[dim]Or use keys: [bold]s[/bold] to skip, [bold]p[/bold] to go back, [bold]f[/bold] to force-complete.[/dim]", id="lbl-inst", classes="control-hint")
                    
                with Vertical(id="preview-panel"):
                    yield Label("Active File Content Preview", classes="panel-title")
                    yield RichLog(id="preview-log", highlight=True, wrap=True)

    def on_mount(self) -> None:
        # Register global hotkey
        if KEYBOARD_AVAILABLE:
            try:
                self.hotkey_hook = keyboard.add_hotkey(self.config["hotkey"], self.on_hotkey, suppress=True)
            except Exception as e:
                log = self.query_one("#preview-log", RichLog)
                log.write(f"[bold red]Error registering hotkey '{self.config['hotkey']}': {e}[/bold red]")
                
        self.set_active_file(0)

    def on_unmount(self) -> None:
        if self.hotkey_hook and KEYBOARD_AVAILABLE:
            keyboard.remove_hotkey(self.hotkey_hook)

    def on_key(self, event) -> None:
        # Support keyboard controls within textual app
        if event.key == "s":
            self.skip_current()
            event.prevent_default()
        elif event.key == "p":
            self.prev_file()
            event.prevent_default()
        elif event.key == "f":
            self.force_complete_current()
            event.prevent_default()

    def set_active_file(self, index: int) -> None:
        if index < 0 or index >= len(self.files):
            return
        
        # Reset previous active file item representation if it was pending or active
        prev_item = self.query_one(f"#file-{self.active_index}", FileItem)
        if prev_item.status == "active":
            prev_item.status = "pending"
            
        self.active_index = index
        file_info = self.files[index]
        
        # Mark new file item as active
        new_item = self.query_one(f"#file-{index}", FileItem)
        new_item.status = "active"
        new_item.scroll_visible()
        
        # Update Labels & Instructions
        lbl_curr = self.query_one("#lbl-current", Label)
        lbl_curr.update(f"Current File: [bold yellow]({index+1}/{len(self.files)}) {file_info['path']}[/bold yellow]")
        
        # Update content preview
        preview = self.query_one("#preview-log", RichLog)
        preview.clear()
        
        if file_info["action"] == "delete":
            preview.write("[bold red]DELETION ACTION DETECTED[/bold red]")
            preview.write(f"File to delete: {file_info['path']}")
            preview.write("Please delete this file manually in your web interface, then press [bold]f[/bold] to mark as completed.")
        else:
            content = safe_read_file(file_info["path"])
            preview.write(content)

    def on_hotkey(self) -> None:
        if self.is_applying or self.active_index >= len(self.files):
            return
        
        self.is_applying = True
        file_info = self.files[self.active_index]
        
        if file_info["action"] == "delete":
            self.is_applying = False
            return
            
        # Extract file contents
        content = safe_read_file(file_info["path"])
        
        # Handle clipboard operation in a safe background context if needed
        try:
            pyperclip.copy(content)
            time.sleep(0.3) # Wait for system clipboard registry
            
            # Backspace the triggering hotkey keystroke to keep environment clean
            keyboard.send('backspace')
            time.sleep(0.1)
            
            # Execute editor overwrite macro
            keyboard.send('ctrl+a')
            time.sleep(0.2)
            keyboard.send('ctrl+v')
            
            # Success, update file status on Textual thread
            self.call_from_thread(self.mark_done_and_advance, self.active_index)
        except Exception as e:
            self.call_from_thread(self.log_error, str(e))
        finally:
            self.is_applying = False

    def mark_done_and_advance(self, index: int) -> None:
        file_item = self.query_one(f"#file-{index}", FileItem)
        file_item.status = "done"
        
        # Advance global progress bar
        pb = self.query_one("#global-progress", ProgressBar)
        pb.advance(1)
        
        if index + 1 < len(self.files):
            self.set_active_file(index + 1)
        else:
            self.query_one("#lbl-current", Label).update("[bold green]All files applied successfully![/bold green] Press Ctrl+C to quit.")

    def skip_current(self) -> None:
        file_item = self.query_one(f"#file-{self.active_index}", FileItem)
        file_item.status = "skipped"
        
        # Advance global progress bar
        pb = self.query_one("#global-progress", ProgressBar)
        pb.advance(1)
        
        if self.active_index + 1 < len(self.files):
            self.set_active_file(self.active_index + 1)

    def force_complete_current(self) -> None:
        self.mark_done_and_advance(self.active_index)

    def prev_file(self) -> None:
        if self.active_index > 0:
            # Regress global progress bar
            pb = self.query_one("#global-progress", ProgressBar)
            pb.advance(-1)
            self.set_active_file(self.active_index - 1)

    def log_error(self, err_msg: str) -> None:
        log = self.query_one("#preview-log", RichLog)
        log.write(f"[bold red]Error applying macro: {err_msg}[/bold red]")


def main():
    parser = argparse.ArgumentParser(description="Web Apply - Apply git modifications to browser environments using keyboard emulation.")
    parser.add_argument("-c", "--commit", help="Git commit hash to diff against HEAD")
    parser.add_argument("-k", "--hotkey", default=None, help="Macro trigger hotkey (e.g. +, ctrl+alt+v)")
    args = parser.parse_args()

    if not KEYBOARD_AVAILABLE:
        print("Error: The 'keyboard' library is required to simulate keyboard inputs.")
        print("Please install it using: pip install keyboard")
        sys.exit(1)

    if not PYCLIPBOARD_AVAILABLE:
        print("Error: The 'pyperclip' library is required to copy text to the clipboard.")
        print("Please install it using: pip install pyperclip")
        sys.exit(1)

    initial_args = vars(args)
    saved_config = load_saved_config()
    
    # Default missing fields
    if not initial_args.get("hotkey") and "hotkey" in saved_config:
        initial_args["hotkey"] = saved_config["hotkey"]
    elif not initial_args.get("hotkey"):
        initial_args["hotkey"] = "+"
        
    if not initial_args.get("commit") and "commit" in saved_config:
        initial_args["commit"] = saved_config["commit"]

    missing_args = not initial_args.get("commit")
    
    if missing_args:
        setup_app = SetupApp(initial_args)
        final_args = setup_app.run()
        if not final_args:
            print("Setup cancelled. Exiting.")
            sys.exit(0)
    else:
        final_args = initial_args

    print(f"Analyzing sync status between {final_args['commit']} and HEAD...")
    files = get_changed_files(final_args["commit"])
    
    if not files:
        print("No file changes detected (nothing to apply).")
        sys.exit(0)

    # Save finalized configuration for future runs
    save_config(final_args["commit"], final_args["hotkey"])

    # Run main TUI app
    app = WebApp(final_args, files)
    app.run()

if __name__ == "__main__":
    main()
