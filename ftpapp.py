import os
import sys
import time
import argparse
import subprocess
import threading
import json
import hashlib
import io
from ftplib import FTP, error_perm

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Input, Label, Button, RichLog, Static, ProgressBar, OptionList
from textual.reactive import reactive

try:
    from textual.widgets import Slider
except ImportError:
    from textual.message import Message

    class Slider(Static, can_focus=True):
        """A simple keyboard-navigable slider fallback for older Textual versions."""
        
        class Changed(Message):
            def __init__(self, slider: "Slider", value: int):
                super().__init__()
                self.slider = slider
                self.value = value

            @property
            def control(self):
                return self.slider

        def __init__(self, min: int = 0, max: int = 10, step: int = 1, value: int = 0, id: str = None, **kwargs):
            super().__init__(id=id, **kwargs)
            self.min = min
            self.max = max
            self.step = step
            self._value = value

        @property
        def value(self) -> int:
            return self._value

        @value.setter
        def value(self, val: int) -> None:
            new_val = max(self.min, min(self.max, val))
            if new_val != self._value:
                self._value = new_val
                self.post_message(self.Changed(self, new_val))
                self.refresh()

        def render(self) -> str:
            total_width = 40
            if self.max <= self.min:
                pos = 0
            else:
                pos = int((self._value - self.min) / (self.max - self.min) * (total_width - 1))
            
            bars = ["━"] * total_width
            if 0 <= pos < total_width:
                bars[pos] = "◆"
            
            track = "".join(bars)
            focus_style = "bold yellow" if self.has_focus else "cyan"
            return f"[{focus_style}]◀━ {track} ━▶ (Use Left/Right keys)[/{focus_style}]"

        def on_key(self, event) -> None:
            if event.key == "left":
                self.value -= self.step
                event.prevent_default()
            elif event.key == "right":
                self.value += self.step
                event.prevent_default()

# --- Utilities ---

def get_changed_files(commit_hash: str) -> list[dict]:
    """Extracts Added, Modified, and Deleted files using git diff against HEAD."""
    try:
        try:
            repo_root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()
        except Exception:
            repo_root = os.getcwd()
            
        # --no-renames breaks renames down into a Deleted event and an Added event
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
                abs_path = os.path.abspath(os.path.join(repo_root, filepath))
                
                if status == 'D':
                    files.append({"path": filepath, "abs_path": abs_path, "action": "delete", "git_status": "D", "base_commit": commit_hash})
                elif status in ('A', 'M'):
                    if os.path.exists(abs_path):
                        files.append({"path": filepath, "abs_path": abs_path, "action": "upload", "git_status": status, "base_commit": commit_hash})
        return files
    except subprocess.CalledProcessError as e:
        print(f"Git error resolving commit {commit_hash}: {e.output}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: Git is not installed or not in PATH.")
        sys.exit(1)


def format_bytes(size: float) -> str:
    """Formats bytes to a human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


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
    dir_path = os.path.expanduser("~/.configs/ftptuı")
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


def save_config(ftp: str, username: str, repo_loc: str, last_commit: str = "") -> None:
    """Saves folder configuration (excluding password but saving last commit hash)."""
    try:
        path = get_config_filepath()
        data = {
            "ftp": ftp,
            "username": username,
            "repo_loc": repo_loc
        }
        if last_commit:
            data["last_commit"] = last_commit
        else:
            existing = load_saved_config()
            if "last_commit" in existing:
                data["last_commit"] = existing["last_commit"]
                
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

# --- Textual Components ---

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

class FileItem(Static):
    """Represents a single file in the queue with a reactive status."""
    
    status = reactive("pending") # pending, uploading, done, error
    
    def __init__(self, filepath: str, action: str = "upload", **kwargs):
        super().__init__(**kwargs)
        self.filepath = filepath
        self.action = action
        self.spinner_idx = 0
        self.spinner_timer = None

    def on_mount(self) -> None:
        self.spinner_timer = self.set_interval(0.1, self.tick_spinner, pause=True)

    def tick_spinner(self) -> None:
        if self.status in ("uploading", "deleting"):
            self.spinner_idx = (self.spinner_idx + 1) % len(SPINNER_FRAMES)
            self.refresh()

    def watch_status(self, old_status: str, new_status: str) -> None:
        if new_status in ("uploading", "deleting"):
            self.spinner_timer.resume()
        else:
            if self.spinner_timer:
                self.spinner_timer.pause()
        self.refresh()

    def render(self) -> str:
        if self.status == "pending":
            icon = "[dim]○[/dim]" if self.action == "upload" else "[dim]🗑[/dim]"
            style = "dim"
        elif self.status in ("uploading", "deleting"):
            icon = f"[bold cyan]{SPINNER_FRAMES[self.spinner_idx]}[/bold cyan]"
            style = "bold cyan"
        elif self.status == "done":
            icon = "[bold green]✓[/bold green]"
            style = "green"
        else:
            icon = "[bold red]✗[/bold red]"
            style = "red"
            
        action_prefix = "[bold red]\\[DEL\\][/bold red] " if self.action == "delete" else ""
        return f"{icon} {action_prefix}[{style}]{self.filepath}[/{style}]"


class SetupApp(App):
    """TUI Form to gather missing CLI arguments."""

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
    OptionList { height: 6; background: #1e1a18; border: tall #5a4d45; margin-top: 1; }
    OptionList:focus { border: tall #d08c60; }
    #btn-start { margin-top: 2; width: 100%; }
    .title { text-align: center; text-style: bold; color: #d08c60; margin-bottom: 1; }
    #lbl-commit-details {
        background: #241f1c;
        padding: 1;
        border-left: solid #d08c60;
        margin-top: 1; 
        margin-bottom: 1;
        height: 3;
    }
    .commit-row {
        height: auto;
        align: left middle;
        margin-top: 1;
    }
    .commit-row OptionList {
        width: 70%;
        margin-top: 0;
    }
    .commit-row Input {
        width: 70%;
    }
    #btn-load-last {
        width: 30%;
        height: 3;
        margin-left: 1;
    }
    """

    def __init__(self, initial_args: dict):
        super().__init__()
        self.initial_args = initial_args
        self.result = None
        self.commits = get_git_commits()

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-container"):
            yield Label("Missing Configuration Arguments", classes="title")
            
            yield Label("FTP Host (e.g. 192.168.1.1 or 192.168.1.1:21)")
            yield Input(value=self.initial_args.get("ftp", ""), id="inp-ftp")
            
            yield Label("Username")
            yield Input(value=self.initial_args.get("username", ""), id="inp-user")
            
            yield Label("Password")
            yield Input(value=self.initial_args.get("password", ""), password=True, id="inp-pass")
            
            if self.commits:
                yield Label("Commit Hash Selection (Use Arrow Keys / Enter)")
                yield Label("", id="lbl-commit-details")
                with Horizontal(classes="commit-row"):
                    yield OptionList(
                        *[f"{c['hash']} - {c['subject']}" for c in self.commits],
                        id="inp-commit-list"
                    )
                    yield Button("Load Last", id="btn-load-last")
            else:
                yield Label("Commit Hash (e.g. HEAD~1, a1b2c3d)")
                with Horizontal(classes="commit-row"):
                    yield Input(value=self.initial_args.get("commit", ""), id="inp-commit")
                    yield Button("Load Last", id="btn-load-last")
            
            yield Label("Repo Location (e.g. /httpdocs/)")
            yield Input(value=self.initial_args.get("repo_loc", ""), id="inp-repo")
            
            yield Button("Start Deployment", variant="success", id="btn-start")

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
        if event.button.id == "btn-load-last":
            last_val = self.initial_args.get("last_commit", "").strip()
            if not last_val:
                self.notify("No previously saved commit found.", severity="warning")
                return

            if self.commits:
                idx = None
                for i, c in enumerate(self.commits):
                    if c["hash"].startswith(last_val) or last_val.startswith(c["hash"]):
                        idx = i
                        break
                if idx is not None:
                    self.query_one("#inp-commit-list", OptionList).highlighted = idx
                    self.notify(f"Selected last commit: {self.commits[idx]['hash']}")
                else:
                    self.notify(f"Last commit {last_val} not in recent 50 commits.", severity="warning")
            else:
                self.query_one("#inp-commit", Input).value = last_val
                self.notify(f"Loaded last commit: {last_val}")

        elif event.button.id == "btn-start":
            if self.commits:
                list_widget = self.query_one("#inp-commit-list", OptionList)
                idx = list_widget.highlighted
                commit_val = self.commits[idx]["hash"] if idx is not None and 0 <= idx < len(self.commits) else ""
            else:
                commit_val = self.query_one("#inp-commit", Input).value.strip()

            self.result = self.initial_args.copy()
            self.result.update({
                "ftp": self.query_one("#inp-ftp", Input).value.strip(),
                "username": self.query_one("#inp-user", Input).value.strip(),
                "password": self.query_one("#inp-pass", Input).value,
                "commit": commit_val,
                "repo_loc": self.query_one("#inp-repo", Input).value.strip()
            })
            # Basic validation
            if not all([self.result["ftp"], self.result["username"], self.result["commit"], self.result["repo_loc"]]):
                self.notify("Please fill in all required fields.", severity="error")
                return
            self.exit(self.result)


class FtpApp(App):
    """Main application to show FTP progress and logs."""

    CSS = """
    Screen { background: #2d2825; }
    #layout { height: 100%; }
    
    #left-panel {
        width: 40%;
        border-right: solid #5a4d45;
        height: 100%;
        background: #241f1c;
    }
    
    #file-list-container { padding: 1 2; }
    
    #right-panel {
        width: 60%;
        height: 100%;
    }
    
    #stats-panel {
        height: 35%;
        border-bottom: solid #5a4d45;
        padding: 1 2;
    }
    
    #log-panel {
        height: 65%;
    }
    
    #ftp-log { height: 1fr; background: #1e1a18; }
    
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
    """

    def __init__(self, config: dict, files: list[dict]):
        super().__init__()
        self.config = config
        self.files = files
        self.total_bytes_transferred = 0
        self.total_size = sum(os.path.getsize(f.get("abs_path", f["path"])) for f in files if f["action"] == "upload" and os.path.exists(f.get("abs_path", f["path"])))

    def compose(self) -> ComposeResult:
        with Horizontal(id="layout"):
            # Left Panel: File Queue
            with Vertical(id="left-panel"):
                yield Label("Files Queue", classes="panel-title")
                with ScrollableContainer(id="file-list-container"):
                    for i, file_info in enumerate(self.files):
                        yield FileItem(file_info["path"], action=file_info["action"], id=f"file-{i}")
                        
            # Right Panel: Stats & Logs
            with Vertical(id="right-panel"):
                with Vertical(id="stats-panel"):
                    yield Label("Transfer Progress", classes="panel-title")
                    yield ProgressBar(total=len(self.files), show_eta=False, id="global-progress")
                    yield Label(f"Speed: 0.0 KB/s", id="lbl-speed", classes="stat-label")
                    yield Label(f"Uploaded: 0 B / {format_bytes(self.total_size)}", id="lbl-uploaded", classes="stat-label")
                    yield Label("Current File: Preparing...", id="lbl-current", classes="stat-label")
                    
                with Vertical(id="log-panel"):
                    yield Label("FTP Protocol Log", classes="panel-title")
                    yield RichLog(id="ftp-log", highlight=True, wrap=True)

    def on_mount(self) -> None:
        # Start the background thread for FTP transfer
        thread = threading.Thread(target=self.run_ftp_transfer, daemon=True)
        thread.start()

    # --- Main FTP Thread ---
    def run_ftp_transfer(self) -> None:
        # Parse host and port
        host_str = self.config["ftp"]
        if ":" in host_str:
            host, port_str = host_str.split(":", 1)
            port = int(port_str)
        else:
            host = host_str
            port = 21
            
        user = self.config["username"]
        pw = self.config["password"]
        repo_loc = self.config["repo_loc"]

        try:
            self.call_from_thread(self._log_msg, f"> CONNECT {host}:{port}")
            ftp = FTP()
            ftp.connect(host, port)
            
            self.call_from_thread(self._log_msg, f"> LOGIN {user}")
            ftp.login(user, pw)
            self.call_from_thread(self._log_msg, "230 User logged in.")

            self.call_from_thread(self._log_msg, f"> CWD {repo_loc}")
            ftp.cwd(repo_loc)
            
            # Get true path to easily return to it, bypassing chroot weirdness
            base_pwd = ftp.pwd()
            self.call_from_thread(self._log_msg, f"250 CWD successful. (Current: {base_pwd})")

            for i, file_info in enumerate(self.files):
                fpath = file_info["path"]
                abs_path = file_info.get("abs_path", fpath)
                action = file_info["action"]
                
                self.call_from_thread(self._start_file, i, fpath, action)
                
                dir_path = os.path.dirname(fpath)
                filename = os.path.basename(fpath)
                
                dir_ok = True
                if dir_path:
                    parts = dir_path.replace('\\', '/').split('/')
                    for p in parts:
                        if not p: continue
                        try:
                            ftp.cwd(p)
                        except error_perm:
                            if action == "upload":
                                self.call_from_thread(self._log_msg, f"> MKD {p}")
                                ftp.mkd(p)
                                self.call_from_thread(self._log_msg, f"> CWD {p}")
                                ftp.cwd(p)
                            else:
                                self.call_from_thread(self._log_msg, f"Directory path '{p}' doesn't exist on server. Skipping deletion.")
                                dir_ok = False
                                break

                if not dir_ok:
                    self.call_from_thread(self._finish_file, i, "error")
                    ftp.cwd(base_pwd)
                    self.call_from_thread(self._advance_global_progress)
                    continue

                proceed = True
                git_status = file_info.get("git_status")
                base_commit = file_info.get("base_commit")
                
                if git_status in ('M', 'D'):
                    if self.config.get("force"):
                        self.call_from_thread(self._log_msg, f"Skipping remote verification for {filename} (--force enabled).")
                    else:
                        self.call_from_thread(self._log_msg, f"Verifying remote state for {filename}...")
                        remote_buffer = io.BytesIO()
                        try:
                            ftp.retrbinary(f'RETR {filename}', remote_buffer.write)
                            remote_content = remote_buffer.getvalue()
                        except error_perm as e:
                            self.call_from_thread(self._log_msg, f"Verification failed: Remote file not found ({e})")
                            proceed = False
                            
                        if proceed:
                            try:
                                git_path = fpath.replace('\\', '/')
                                base_content = subprocess.check_output(
                                    ['git', 'show', f'{base_commit}:{git_path}'],
                                    stderr=subprocess.STDOUT
                                )
                                remote_norm = remote_content.replace(b'\r\n', b'\n')
                                base_norm = base_content.replace(b'\r\n', b'\n')
                                
                                if remote_norm != base_norm:
                                    self.call_from_thread(self._log_msg, f"Verification failed: Remote file does not match base commit {base_commit}")
                                    proceed = False
                            except subprocess.CalledProcessError as e:
                                self.call_from_thread(self._log_msg, f"Verification failed: Could not read {git_path} at {base_commit}")
                                proceed = False

                if not proceed:
                    self.call_from_thread(self._finish_file, i, "error")
                    ftp.cwd(base_pwd)
                    self.call_from_thread(self._advance_global_progress)
                    continue

                if action == "upload":
                    local_size = os.path.getsize(abs_path)
                    self.call_from_thread(self._log_msg, f"> STOR {filename} ({format_bytes(local_size)})")
                    
                    start_time = time.time()
                    last_ui_update = 0
                    file_uploaded = 0

                    def upload_callback(chunk):
                        nonlocal file_uploaded, last_ui_update
                        chunk_len = len(chunk)
                        file_uploaded += chunk_len
                        self.total_bytes_transferred += chunk_len
                        
                        now = time.time()
                        if now - last_ui_update > 0.1:
                            dt = now - start_time
                            speed = (file_uploaded / dt) if dt > 0 else 0
                            self.call_from_thread(self._update_stats, speed)
                            last_ui_update = now

                    try:
                        with open(abs_path, 'rb') as f:
                            ftp.storbinary(f'STOR {filename}', f, blocksize=8192, callback=upload_callback)
                        
                        dt = time.time() - start_time
                        final_speed = (file_uploaded / dt) if dt > 0 else 0
                        self.call_from_thread(self._update_stats, final_speed)
                        
                        self.call_from_thread(self._log_msg, "226 Transfer complete.")
                        self.call_from_thread(self._finish_file, i, "done")
                    except Exception as e:
                        self.call_from_thread(self._log_msg, f"ERROR STORing {filename}: {e}")
                        self.call_from_thread(self._finish_file, i, "error")
                elif action == "delete":
                    self.call_from_thread(self._log_msg, f"> DELE {filename}")
                    try:
                        ftp.delete(filename)
                        self.call_from_thread(self._log_msg, "250 DELE command successful.")
                        self.call_from_thread(self._finish_file, i, "done")
                    except error_perm as e:
                        self.call_from_thread(self._log_msg, f"FTP Server Warning during DELE {filename}: {e}")
                        self.call_from_thread(self._finish_file, i, "done")
                    except Exception as e:
                        self.call_from_thread(self._log_msg, f"ERROR DELETIng {filename}: {e}")
                        self.call_from_thread(self._finish_file, i, "error")

                ftp.cwd(base_pwd)
                self.call_from_thread(self._advance_global_progress)

            ftp.quit()
            self.call_from_thread(self._log_msg, "> QUIT")
            self.call_from_thread(self._log_msg, "221 Goodbye.")
            self.call_from_thread(self._finish_all)

        except Exception as e:
            self.call_from_thread(self._log_msg, f"CRITICAL ERROR: {e}")
            self.call_from_thread(self._update_current_label, "[bold red]Transfer Aborted due to error.[/bold red]")

    # --- UI Update Helpers (Must be called from thread) ---

    def _log_msg(self, msg: str) -> None:
        log = self.query_one("#ftp-log", RichLog)
        log.write(msg)

    def _start_file(self, idx: int, filepath: str, action: str) -> None:
        file_item = self.query_one(f"#file-{idx}", FileItem)
        file_item.status = "uploading" if action == "upload" else "deleting"
        file_item.scroll_visible()
        verb = "Uploading" if action == "upload" else "Deleting remote"
        self._update_current_label(f"Current File: [bold cyan]{verb} {filepath}[/bold cyan]")

    def _update_stats(self, speed_bps: float) -> None:
        lbl_speed = self.query_one("#lbl-speed", Label)
        lbl_speed.update(f"Speed: [bold green]{format_bytes(speed_bps)}/s[/bold green]")
        
        lbl_uploaded = self.query_one("#lbl-uploaded", Label)
        lbl_uploaded.update(f"Uploaded: [bold]{format_bytes(self.total_bytes_transferred)}[/bold] / {format_bytes(self.total_size)}")

    def _update_current_label(self, msg: str) -> None:
        self.query_one("#lbl-current", Label).update(msg)

    def _finish_file(self, idx: int, status: str) -> None:
        file_item = self.query_one(f"#file-{idx}", FileItem)
        file_item.status = status

    def _advance_global_progress(self) -> None:
        pb = self.query_one("#global-progress", ProgressBar)
        pb.advance(1)

    def _finish_all(self) -> None:
        self._update_current_label("[bold green]All transfers complete![/bold green] Press Ctrl+C to exit.")
        lbl_speed = self.query_one("#lbl-speed", Label)
        lbl_speed.update("Speed: 0 B/s")


def main():
    parser = argparse.ArgumentParser(description="FTP Applier - Upload git changes via FTP.")
    parser.add_argument("-f", "--ftp", help="FTP connection ip and port (e.g., 192.168.1.1:21)")
    parser.add_argument("-u", "--username", help="FTP username")
    parser.add_argument("-p", "--password", help="FTP password")
    parser.add_argument("-c", "--commit", help="Git commit hash to diff against HEAD")
    parser.add_argument("-r", "--repo-loc", help="Repository location on FTP server")
    parser.add_argument("-s", "--select", action="store_true", help="Open TUI to select which files to update")
    parser.add_argument("--force", action="store_true", help="Force changes by skipping remote state verification")
    args = parser.parse_args()

    initial_args = vars(args)

    # Load and merge saved configuration
    saved_config = load_saved_config()
    for key in ["ftp", "username", "repo_loc", "last_commit"]:
        if not initial_args.get(key) and key in saved_config:
            initial_args[key] = saved_config[key]
    
    # Determine if we need to show the setup TUI
    missing_args = False
    for key in ["ftp", "username", "commit", "repo_loc"]:
        if not initial_args.get(key):
            missing_args = True
            break

    if missing_args:
        setup_app = SetupApp(initial_args)
        final_args = setup_app.run()
        if not final_args:
            print("Setup cancelled. Exiting.")
            sys.exit(0)
    else:
        final_args = initial_args
        # Handle None password to empty string
        if final_args.get("password") is None:
            final_args["password"] = ""

    # Now we have all configs, grab the git diff files
    print(f"Analyzing sync status between {final_args['commit']} and HEAD...")
    files = get_changed_files(final_args["commit"])
    
    if not files:
        print("No file changes detected (nothing to upload or delete).")
        sys.exit(0)

    if args.select:
        try:
            repo_root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()
        except Exception:
            repo_root = os.getcwd()

        file_paths = [f["abs_path"] for f in files]
        from tui_selection import run_file_selector
        selected = run_file_selector(repo_root, file_paths, ast_mode=False)
        
        if selected is None:
            print("Selection cancelled. Exiting.")
            sys.exit(0)
            
        selected_files, _, _ = selected
        files = [f for f in files if f["abs_path"] in selected_files]
        
        if not files:
            print("No files selected. Exiting.")
            sys.exit(0)

    # Save finalized configuration for next run
    save_config(final_args["ftp"], final_args["username"], final_args["repo_loc"], final_args["commit"])

    # Launch Main App
    app = FtpApp(final_args, files)
    app.run()

if __name__ == "__main__":
    main()
