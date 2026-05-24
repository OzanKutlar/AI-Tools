import os
import time
import json
import difflib
import re
import subprocess
import threading
import tempfile
import shutil
from rich.text import Text
from rich.style import Style
import pyperclip

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Label, ListView, ListItem, Button, Static, RichLog, TextArea, Markdown
from textual.binding import Binding
from textual.screen import ModalScreen

from cc_utils import (
    safe_read_file,
    intelligent_json_fix,
    render_word_diff,
    copy_to_clipboard,
    copy_file_to_clipboard
)

from cc_prompts import DEFAULT_SYSTEM_PROMPT_TEMPLATE

class HumanCorrectScreen(ModalScreen[str]):
    CSS = """
    HumanCorrectScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.8);
    }
    #hc-dialog {
        width: 95%;
        height: 95%;
        border: solid #d08c60;
        background: #2d2825;
    }
    #hc-body {
        height: 1fr;
    }
    #hc-left {
        width: 25%;
        border-right: solid #5a4d45;
    }
    #hc-right {
        width: 75%;
    }
    #hc-search-pane {
        height: 40%;
        border-bottom: solid #5a4d45;
    }
    #hc-file-pane {
        height: 60%;
    }
    .hc-title {
        background: #4a3f39;
        color: #d08c60;
        padding: 1;
        text-style: bold;
    }
    #hc-footer {
        height: 3; 
        border-top: solid #5a4d45;
        align: right middle;
    }
    Button {
        margin: 0 1;
    }
    """

    def __init__(self, file_path: str, file_text: str, original_search: str, candidates: list, replace_text: str):
        super().__init__()
        self.file_path = file_path
        self.file_text = file_text
        self.original_search = original_search
        self.candidates = candidates
        self.replace_text = replace_text

    def compose(self) -> ComposeResult:
        with Vertical(id="hc-dialog"):
            with Horizontal(id="hc-body"):
                with Vertical(id="hc-left"):
                    yield Label("Partial Matches", classes="hc-title")
                    yield ListView(
                        *[ListItem(Label(f"Match lines {c['start_line']}-{c['end_line']}"), id=f"cand-{i}") for i, c in enumerate(self.candidates)],
                        id="hc-cand-list"
                    )
                with Vertical(id="hc-right"):
                    with Vertical(id="hc-search-pane"):
                        yield Label("Diff: Original Search vs Selected Candidate", classes="hc-title")
                        yield RichLog(id="hc-diff-view", highlight=True)
                    with Vertical(id="hc-file-pane"):
                        yield Label("File Content (Select the correct area and press Confirm)", classes="hc-title")
                        yield TextArea(self.file_text, id="hc-file-text")
            with Horizontal(id="hc-footer"):
                yield Button("Confirm Selection", id="btn-confirm", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="error")

    def on_mount(self) -> None:
        if self.candidates:
            self.query_one("#hc-cand-list", ListView).index = 0
            self.action_scroll_to_candidate(0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and event.item.id and event.item.id.startswith("cand-"):
            idx = int(event.item.id.split("-")[1])
            self.action_scroll_to_candidate(idx)
            
    def action_scroll_to_candidate(self, idx: int) -> None:
        if 0 <= idx < len(self.candidates):
            c = self.candidates[idx]
            file_ta = self.query_one("#hc-file-text", TextArea)
            file_ta.move_cursor((c["start_line"] - 1, 0))
            file_ta.scroll_cursor_visible()
            self._render_candidate_diff(idx)

    def _render_candidate_diff(self, idx: int) -> None:
        c = self.candidates[idx]
        file_lines = self.file_text.splitlines(keepends=True)
        cand_lines = file_lines[c["start_line"] - 1 : c["end_line"]]
        cand_text = "".join(cand_lines)
        search_text = self.original_search
        
        diff_view = self.query_one("#hc-diff-view", RichLog)
        diff_view.clear()
        if search_text == cand_text:
            diff_view.write(Text("No changes detected.", style="dim"))
        else:
            render_word_diff(search_text, cand_text, diff_view)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            file_ta = self.query_one("#hc-file-text", TextArea)
            selected_text = file_ta.selected_text
            if selected_text:
                self.dismiss(selected_text)
            else:
                self.app.notify("Please select some text in the File Content area first.", severity="error")
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

class MacroScreen(ModalScreen):
    """TUI for applying changes step-by-step using a keyboard macro or clipboard interception."""
    CSS = """
    MacroScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.8);
    }
    #macro-dialog {
        width: 75%;
        height: auto;
        max-height: 90%;
        border: solid #d08c60;
        background: #2d2825;
        padding: 1 2;
    }
    .macro-title {
        text-align: center;
        text-style: bold;
        color: #d08c60;
        margin-bottom: 1;
    }
    .macro-inst {
        text-align: center;
        text-style: bold;
        color: #ead6c9;
        margin-bottom: 1;
    }
    .macro-sub {
        text-align: center;
        color: #a0a0a0;
        margin-bottom: 1;
    }
    .macro-error {
        color: #ff5555;
        text-style: bold;
        margin: 1 0;
    }
    #macro-text-display {
        height: 12;
        border: tall #5a4d45;
        background: #1e1a18;
        color: #ead6c9;
        margin: 1 0;
    }
    #macro-diff-display {
        height: 12;
        border: tall #5a4d45;
        background: #1e1a18;
        margin: 1 0;
        display: none;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "force_advance", "Force Next Step", show=True),
        Binding("space", "force_advance", "Force Next Step", show=False)
    ]

    def __init__(self, payload: dict, indices: list[int]):
        super().__init__()
        self.payload = payload
        self.indices = indices
        self.steps = []
        self.current_step_idx = 0
        self.is_executing = False
        self.hook = None
        self.completed_file_indices = set()
        self._build_steps()

    def _build_steps(self) -> None:
        for idx in self.indices:
            file_obj = self.payload.get("files", [])[idx]
            action = file_obj.get("action", "modify").upper()
            path = file_obj.get("path", "unknown")

            if action == "CREATE":
                self.steps.append({"type": "CREATE", "path": path, "content": file_obj.get("content", ""), "file_idx": idx})
            elif action == "DELETE":
                self.steps.append({"type": "DELETE", "path": path, "file_idx": idx})
            elif action == "MODIFY":
                if "content" in file_obj:
                    self.steps.append({"type": "CREATE", "path": path, "content": file_obj["content"], "file_idx": idx, "desc": "Overwrite file"})
                else:
                    self.steps.append({
                        "type": "MODIFY_WEB",
                        "path": path,
                        "blocks": file_obj.get("search_replace", []),
                        "regex_blocks": file_obj.get("regex_replace", []),
                        "file_idx": idx,
                        "sub_state": "WAITING_COPY"
                    })

    def compose(self) -> ComposeResult:
        with Vertical(id="macro-dialog"):
            yield Label("Web Assistant Mode Active", classes="macro-title")
            yield Label("", id="macro-file", classes="macro-inst")
            yield Label("", id="macro-action", classes="macro-inst")
            yield Label("", id="macro-error-label", classes="macro-error")
            yield TextArea(id="macro-text-display", read_only=True)
            yield RichLog(id="macro-diff-display", highlight=True)
            yield Label("", id="macro-trigger-label", classes="macro-sub")
            yield Label("", id="macro-progress", classes="macro-sub")

    def on_mount(self) -> None:
        if KEYBOARD_AVAILABLE:
            self.hook = keyboard.add_hotkey('+', self.on_hotkey, suppress=True)
        self.last_clipboard = ""
        self.set_interval(0.5, self.check_clipboard_poll)
        self._render_step()

    def on_unmount(self) -> None:
        if self.hook and KEYBOARD_AVAILABLE:
            keyboard.remove_hotkey(self.hook)

    def _render_step(self) -> None:
        if self.current_step_idx >= len(self.steps):
            self.dismiss(list(self.completed_file_indices))
            return
            
        step = self.steps[self.current_step_idx]
        stype = step["type"]
        
        self.query_one("#macro-file", Label).update(f"File: [bold cyan]{step['path']}[/bold cyan]")
        self.query_one("#macro-error-label", Label).update("")
        
        action_text = ""
        text_to_show = ""
        trigger_hint = "Press [bold green]+[/bold green] or [bold green]Enter[/bold green] to continue."

        text_display = self.query_one("#macro-text-display", TextArea)
        diff_display = self.query_one("#macro-diff-display", RichLog)
        
        text_display.display = True
        diff_display.display = False

        if stype == "CREATE" or "desc" in step:
            action_text = f"Action: Create/Overwrite File"
            text_to_show = step.get("content", "")
            trigger_hint = "Press [bold green]+[/bold green] inside your editor to Paste (or Copy from here manually)."
        elif stype == "DELETE":
            action_text = f"Action: Delete File"
            trigger_hint = "Please delete the file manually, then press [bold green]Enter[/bold green] here."
        elif stype == "MODIFY_WEB":
            if step.get("sub_state") == "WAITING_COPY":
                action_text = "[yellow]Action: COPY ENTIRE FILE[/yellow]"
                text_to_show = "1. Go to your IDE/Editor.\n2. Select All (Ctrl+A).\n3. Copy (Ctrl+C)."
                trigger_hint = "[bold cyan]Waiting for clipboard...[/bold cyan] (Or press Enter to force)"
            elif step.get("sub_state") == "REVIEW_DIFF":
                action_text = "[magenta]Action: REVIEW CHANGES[/magenta]"
                errs = step.get("errors", [])
                if errs:
                    self.query_one("#macro-error-label", Label).update(f"MISSING BLOCKS IN {step.get('path', 'unknown')}:\n" + "\n".join(errs))
                trigger_hint = "Press [bold green]Enter[/bold green] to accept changes and copy to clipboard."
                text_display.display = False
                diff_display.display = True
                diff_display.clear()
                render_word_diff(step["old_text"], step["new_text"], diff_display)
                self.query_one("#macro-action", Label).update(action_text)
                self.query_one("#macro-trigger-label", Label).update(trigger_hint)
                self.query_one("#macro-progress", Label).update(f"Step {self.current_step_idx + 1} of {len(self.steps)}")
                return
            else:
                action_text = "[green]Action: PASTE MODIFIED FILE[/green]"
                errs = step.get("errors", [])
                if errs:
                    self.query_one("#macro-error-label", Label).update(f"MISSING BLOCKS IN {step.get('path', 'unknown')}:\n" + "\n".join(errs))
                text_to_show = "Modified content is on your clipboard.\n\n1. Go back to your editor.\n2. Make sure everything is still selected (Ctrl+A).\n3. Paste (Ctrl+V)."
                trigger_hint = "Press [bold green]Enter[/bold green] here once you have pasted the changes."
            
        self.query_one("#macro-action", Label).update(action_text)
        text_display.load_text(text_to_show)
        self.query_one("#macro-trigger-label", Label).update(trigger_hint)
        self.query_one("#macro-progress", Label).update(f"Step {self.current_step_idx + 1} of {len(self.steps)}")

    def check_clipboard_poll(self) -> None:
        if self.current_step_idx >= len(self.steps) or self.is_executing:
            return
        step = self.steps[self.current_step_idx]
        if step["type"] == "MODIFY_WEB" and step.get("sub_state") == "WAITING_COPY":
            try:
                current = pyperclip.paste()
                if current and current != self.last_clipboard:
                    blocks = step.get("blocks", [])
                    is_likely_file = False
                    if not blocks:
                        is_likely_file = True
                    else: 
                        for b in blocks[:5]:
                            if b.get("search", "") in current:
                                is_likely_file = True
                                break
                    if is_likely_file:
                        self.last_clipboard = current
                        self._trigger_processing(current)
            except Exception:
                pass

    def action_force_advance(self) -> None:
        if self.is_executing:
            return
        step = self.steps[self.current_step_idx]
        if step["type"] == "MODIFY_WEB":
            if step.get("sub_state") == "WAITING_COPY":
                self._trigger_processing(pyperclip.paste())
            elif step.get("sub_state") == "REVIEW_DIFF":
                new_text = step["new_text"]
                pyperclip.copy(new_text)
                self.last_clipboard = new_text
                step["sub_state"] = "WAITING_PASTE"
                self._render_step()
            else:
                self._advance()
        else:
            self._advance()

    def on_hotkey(self) -> None:
        if self.is_executing:
            return
        step = self.steps[self.current_step_idx]
        if step["type"] == "MODIFY_WEB":
            if step.get("sub_state") == "REVIEW_DIFF":
                new_text = step["new_text"]
                pyperclip.copy(new_text)
                self.last_clipboard = new_text
                step["sub_state"] = "WAITING_PASTE"
                self.app.call_from_thread(self._render_step)
            return
        self.is_executing = True
        try:
            time.sleep(0.4)
            keyboard.send('backspace')
            if step["type"] == "CREATE" or "desc" in step:
                pyperclip.copy(step["content"])
                time.sleep(0.1)
                keyboard.send('ctrl+v')
        except Exception:
            pass
        self.app.call_from_thread(self._advance)

    def _trigger_processing(self, content: str) -> None:
        self.is_executing = True
        step = self.steps[self.current_step_idx]
        new_text = content.replace('\r\n', '\n')
        errors = []
        
        def _norm(t):
            return "\n".join(l.strip() for l in t.strip().split('\n') if l.strip())

        for i, block in enumerate(step.get("blocks", [])):
            s = block.get("search", "")
            r = block.get("replace", "")
            if s and s in new_text:
                new_text = new_text.replace(s, r, 1)
            else:
                ns = _norm(s)
                lines = s.strip('\n').split('\n')
                source_lines = new_text.split('\n')
                found = False
                for j in range(len(source_lines) - len(lines) + 1):
                    window = '\n'.join(source_lines[j : j + len(lines)])
                    if _norm(window) == ns:
                        new_text = new_text.replace(window, r, 1)
                        found = True
                        break
                if not found:
                    errors.append(f"Block {i+1} not found.")

        for i, block in enumerate(step.get("regex_blocks", [])):
            pattern = block.get("pattern", "")
            replacement = block.get("replacement", "")
            if pattern:
                try:
                    new_text = re.sub(pattern, replacement, new_text)
                except re.error as e:
                    errors.append(f"Regex block {i+1} error: {e}")
        step["errors"] = errors
        step["new_text"] = new_text
        step["old_text"] = content
        step["sub_state"] = "REVIEW_DIFF"
        self.is_executing = False
        self._render_step()

    def _advance(self) -> None:
        if self.current_step_idx >= len(self.steps):
            return
        step = self.steps[self.current_step_idx]
        file_idx = step["file_idx"]
        is_last_for_file = True
        for upcoming in self.steps[self.current_step_idx + 1:]:
            if upcoming["file_idx"] == file_idx:
                is_last_for_file = False
                break
        if is_last_for_file:
            self.completed_file_indices.add(file_idx)
        self.current_step_idx += 1
        self.is_executing = False
        self._render_step()

    def action_cancel(self) -> None:
        self.dismiss(list(self.completed_file_indices))

class PartialAddScreen(ModalScreen[str]):
    CSS = """
    PartialAddScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.8);
    }
    #pa-dialog {
        width: 95%;
        height: 95%;
        border: solid #d08c60;
        background: #2d2825;
    }
    #pa-body {
        height: 1fr;
    }
    #pa-diff-pane {
        width: 50%;
        border-right: solid #5a4d45;
    }
    #pa-preview-pane {
        width: 50%;
    }
    .pa-title {
        background: #4a3f39;
        color: #d08c60;
        padding: 1;
        text-style: bold;
    }
    #pa-footer {
        height: 3; 
        border-top: solid #5a4d45;
        align: right middle;
    }
    Button {
        margin: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("w", "prev_change", "Previous", show=False),
        Binding("a", "prev_change", "Previous"),
        Binding("s", "next_change", "Next", show=False),
        Binding("d", "next_change", "Next"),
        Binding("space", "toggle_current", "Toggle"),
    ]

    def __init__(self, file_path: str, old_text: str, new_text: str):
        super().__init__()
        self.file_path = file_path
        self.old_text = old_text
        self.new_text = new_text
        self.old_lines = old_text.splitlines(keepends=True)
        self.new_lines = new_text.splitlines(keepends=True)
        self.tokens = []
        self.atoms = []
        self.cursor_index = 0

    def on_mount(self) -> None:
        log = self.query_one("#pa-hunk-diff", RichLog)
        log.auto_scroll = False
        matcher = difflib.SequenceMatcher(None, self.old_lines, self.new_lines)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                if i2 - i1 > 6:
                    if i1 != 0:
                        self.tokens.append("".join(self.old_lines[i1:i1+3]))
                    skip_start = i1 + 3 if i1 != 0 else i1
                    skip_end = i2 - 3 if i2 != len(self.old_lines) else i2
                    if skip_end > skip_start:
                        self.tokens.append({"type": "skip", "text": "".join(self.old_lines[skip_start:skip_end])})
                    if i2 != len(self.old_lines):
                        self.tokens.append("".join(self.old_lines[i2-3:i2]))
                else:
                    self.tokens.append("".join(self.old_lines[i1:i2]))
            else:
                old_hunk = "".join(self.old_lines[i1:i2])
                new_hunk = "".join(self.new_lines[j1:j2])
                old_words = re.findall(r'\S+|\s+', old_hunk)
                new_words = re.findall(r'\S+|\s+', new_hunk)
                word_matcher = difflib.SequenceMatcher(None, old_words, new_words)
                for w_tag, wi1, wi2, wj1, wj2 in word_matcher.get_opcodes():
                    if w_tag == 'equal':
                        self.tokens.append("".join(old_words[wi1:wi2]))
                    else:
                        atom = {
                            "id": len(self.atoms),
                            "tag": w_tag,
                            "old_text": "".join(old_words[wi1:wi2]),
                            "new_text": "".join(new_words[wj1:wj2]),
                            "accepted": True
                        }
                        self.atoms.append(atom)
                        self.tokens.append(atom)
        self._update_diff_view()
        self._update_preview()

    def action_next_change(self) -> None:
        if not self.atoms: return
        self.cursor_index = (self.cursor_index + 1) % len(self.atoms)
        self._update_diff_view()
        self._update_preview()
        
    def action_prev_change(self) -> None:
        if not self.atoms: return
        self.cursor_index = (self.cursor_index - 1) % len(self.atoms)
        self._update_diff_view()
        self._update_preview()
        
    def action_toggle_current(self) -> None:
        if not self.atoms or self.cursor_index is None: return
        self.atoms[self.cursor_index]["accepted"] = not self.atoms[self.cursor_index]["accepted"]
        self._update_diff_view()
        self._update_preview()

    def action_toggle_atom(self, a_id_str: str) -> None:
        a_id = int(a_id_str)
        if 0 <= a_id < len(self.atoms):
            self.cursor_index = a_id
            self.atoms[a_id]["accepted"] = not self.atoms[a_id]["accepted"]
            self._update_diff_view()
            self._update_preview()

    def _update_diff_view(self) -> None:
        log = self.query_one("#pa-hunk-diff", RichLog)
        log.clear()
        current_line = Text()
        lines_written = 0
        target_line = 0
        
        def process_string(s: str, base_style: str, a_id: int = None):
            nonlocal current_line, lines_written, target_line
            parts = s.split('\n')
            style = Style.parse(base_style) if base_style else Style()
            if a_id is not None:
                style = style + Style(meta={"@click": f"toggle_atom('{a_id}')"})
                if a_id == self.cursor_index:
                    style = style + Style(reverse=True)
                    target_line = lines_written
            for i, part in enumerate(parts):
                if i > 0:
                    log.write(current_line)
                    lines_written += 1
                    current_line = Text()
                if part:
                    current_line.append(part, style=style)
                    
        for token in self.tokens:
            if isinstance(token, str):
                process_string(token, "")
            elif isinstance(token, dict) and token.get("type") == "skip":
                process_string("\n...\n", "bold dim")
            else:
                a_id = token["id"]
                if token["accepted"]:
                    if token["tag"] == "delete":
                        process_string(token["old_text"], "bold red strike", a_id)
                    elif token["tag"] in ("insert", "replace"):
                        process_string(token["new_text"], "bold green", a_id)
                else:
                    if token["tag"] == "insert":
                        process_string(token["new_text"], "dim red strike", a_id)
                    elif token["tag"] in ("replace", "delete"):
                        process_string(token["old_text"], "bold red", a_id)
        if len(current_line) > 0:
            log.write(current_line)
        def do_scroll():
            half_height = (log.size.height // 2) if log.size.height > 0 else 15
            log.scroll_y = max(0, target_line - half_height)
        self.set_timer(0.05, do_scroll)

    def _update_preview(self) -> None:
        res = []
        current_row = 0
        target_row = 0
        for t in self.tokens:
            text_to_add = ""
            if isinstance(t, str):
                text_to_add = t
            elif isinstance(t, dict) and t.get("type") == "skip":
                text_to_add = t["text"]
            else:
                if t.get("id") == self.cursor_index:
                    target_row = current_row
                if t["accepted"]:
                    if t["tag"] in ("insert", "replace"):
                        text_to_add = t["new_text"]
                else:
                    if t["tag"] in ("delete", "replace"):
                        text_to_add = t["old_text"]
            res.append(text_to_add)
            current_row += text_to_add.count('\n')
        
        ta = self.query_one("#pa-preview", TextArea)
        ta.load_text("".join(res))
        ta.move_cursor((target_row, 0))
        ta.scroll_cursor_visible(center=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-pa-accept-all":
            for atom in self.atoms:
                atom["accepted"] = True
            self._update_diff_view()
            self._update_preview()
        elif btn_id == "btn-pa-reject-all":
            for atom in self.atoms:
                atom["accepted"] = False
            self._update_diff_view()
            self._update_preview()
        elif btn_id == "btn-pa-apply":
            res = []
            for t in self.tokens:
                if isinstance(t, str):
                    res.append(t)
                elif isinstance(t, dict) and t.get("type") == "skip":
                    res.append(t["text"])
                else:
                    if t["accepted"]:
                        if t["tag"] in ("insert", "replace"):
                            res.append(t["new_text"])
                    else:
                        if t["tag"] in ("delete", "replace"):
                            res.append(t["old_text"])
            self.dismiss("".join(res))
        elif btn_id == "btn-pa-cancel":
            self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="pa-dialog"):
            with Horizontal(id="pa-body"):
                with Vertical(id="pa-diff-pane"):
                    yield Label(f"Diff for {os.path.basename(self.file_path)} (Click red/green words to toggle!)", classes="pa-title")
                    yield RichLog(id="pa-hunk-diff", highlight=True, wrap=True)
                with Vertical(id="pa-preview-pane"):
                    yield Label("Live Preview (Resulting File)", classes="pa-title")
                    yield TextArea(id="pa-preview", read_only=True)
            with Horizontal(id="pa-footer"):
                yield Button("Accept All", id="btn-pa-accept-all", variant="success")
                yield Button("Reject All", id="btn-pa-reject-all", variant="error")
                yield Button("Apply Custom", id="btn-pa-apply", variant="primary")
                yield Button("Cancel", id="btn-pa-cancel", variant="default")

class OrchestratorAgentApp(App):
    """TUI for monitoring clipboard and applying AI orchestration changes."""
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #layout { height: 100%; }
    #sidebar {
        width: 30%;
        border-right: solid #5a4d45;
        background: #241f1c;
    }
    #sidebar ListView { height: 1fr; }
    #sidebar ListItem { height: auto; }
    #main-area {
        width: 70%;
        padding: 1 2;
    }
    .panel-title {
        background: #4a3f39;
        color: #d08c60;
        text-align: center;
        padding: 1; 
        text-style: bold;
    }
    .action-row {
        height: 3; 
        margin-bottom: 1;
        align: right middle;
    }
    Button { margin-left: 1; }
    #prompt-view {
        margin-top: 1;
        height: 1fr;
        border: solid #5a4d45;
        background: #241f1c;
    }
    #prompt-view:focus {
        border: double #d08c60;
    }
    #ai-markdown {
        height: auto;
        max-height: 40%;
        overflow-y: auto;
        border-bottom: solid #5a4d45;
        margin-bottom: 1;
    }
    """
    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("g", "generate", "Generate Payload"),
        Binding("r", "reload", "Reload Clipboard"),
    ]
    TITLE = "CombineCopy — Orchestrator Listener"

    def __init__(self, root_dir: str, use_file_clipboard: bool = False):
        super().__init__()
        self.root_dir = root_dir
        self.use_file_clipboard = use_file_clipboard
        self.payload = None
        self.last_clipboard = ""
        self.polling_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Label("Waiting for Orchestrator AI...", id="status-label", classes="panel-title")
                yield ListView(id="file-list")
            with Vertical(id="main-area"):
                with Horizontal(classes="action-row"):
                    yield Button("Generate Payload (g)", id="btn-generate", variant="success", disabled=True)
                yield Markdown("*(AI thoughts will appear here)*", id="ai-markdown")
                yield Label("Downstream Prompt:", classes="panel-title")
                yield TextArea(id="prompt-view", read_only=True)
        yield Footer()

    def on_mount(self) -> None:
        self.polling_timer = self.set_interval(0.5, self.check_clipboard)

    def _extract_json_objects(self, text: str) -> list[str]:
        results = []
        idx = 0
        while idx < len(text):
            start_idx = text.find('{', idx)
            if start_idx == -1:
                break
            depth = 0
            in_string = False
            escape_next = False
            end_idx = -1
            for i in range(start_idx, len(text)):
                char = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break
            if end_idx != -1:
                results.append(text[start_idx:end_idx + 1])
                idx = end_idx + 1
            else:
                idx = start_idx + 1
        return results

    def check_clipboard(self) -> None:
        try:
            content = pyperclip.paste().strip()
            if not content or content == self.last_clipboard:
                return
            self.last_clipboard = content
            
            if '"phase":' in content and ('"ORCHESTRATE"' in content or '"EXPLORATION"' in content):
                json_blocks = self._extract_json_objects(content)
                for json_str in json_blocks:
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict):
                            if data.get("phase") == "ORCHESTRATE" and "prompt" in data:
                                self.load_payload(data)
                                return
                            elif data.get("phase") == "EXPLORATION" and "request_files" in data:
                                self.handle_exploration(data)
                                return
                    except Exception:
                        continue
        except Exception:
            pass

    def handle_exploration(self, data: dict) -> None:
        self.polling_timer.pause()
        self.query_one("#status-label", Label).update("[bold cyan]EXPLORATION Requested[/bold cyan]")
        requested_files = data.get("request_files", [])
        
        buffer = []
        buffer.append("--- REQUESTED FILE CONTEXT ---")
        separator = "-" * 35
        
        found_any = False
        for path in requested_files:
            full_path = os.path.abspath(os.path.join(self.root_dir, path))
            if not full_path.startswith(self.root_dir):
                continue
                
            buffer.append(separator)
            buffer.append(f"FILE: {path}")
            buffer.append(separator)
            _, ext = os.path.splitext(path)
            lang = ext.lstrip('.').lower()
            buffer.append(f"```{lang}")
            if os.path.exists(full_path):
                try:
                    buffer.append(safe_read_file(full_path))
                    found_any = True
                except Exception as e:
                    buffer.append(f"[Error reading file: {e}]")
            else:
                buffer.append(f"[File not found: {path}]")
            buffer.append("```")
            buffer.append("")
            
        if not found_any:
            buffer.append("\n(No files found matching the request.)")
            
        buffer.append("\n--- SYSTEM REMINDER ---")
        buffer.append("If you still need more files, output another EXPLORATION payload.")
        buffer.append("If you have enough context, enter PLANNING mode to design your approach.")
        
        final_text = "\n".join(buffer)
        
        if copy_to_clipboard(final_text):
            self.notify(f"Fetched {len(requested_files)} files. Copied to clipboard! Please paste back to the LLM.", title="Exploration")
            self.last_clipboard = final_text
            
        self.query_one("#ai-markdown", Markdown).update(f"**The AI requested full context for {len(requested_files)} files.**\n\nThese have been fetched and copied to your clipboard. Paste them back to the AI.")
        
        self.polling_timer.resume()
        self.query_one("#status-label", Label).update("Waiting for Orchestrator AI...")

    def load_payload(self, data: dict) -> None:
        self.polling_timer.pause()
        self.payload = data
        self.query_one("#status-label", Label).update("Orchestrator Payload Ready")
        self.query_one("#ai-markdown", Markdown).update(data.get("markdown", "No markdown provided."))
        
        original = data.get("original_request", "")
        instr = data.get("prompt", "")
        display_parts = []
        if original:
            display_parts.append(f"--- ORIGINAL REQUEST ---\n{original}")
        if instr:
            display_parts.append(f"--- ORCHESTRATOR INSTRUCTIONS ---\n{instr}")
            
        self.query_one("#prompt-view", TextArea).text = "\n\n".join(display_parts)
        file_list = self.query_one("#file-list", ListView)
        file_list.clear()
        for f in data.get("files", []):
            file_list.append(ListItem(Label(f)))
        self.query_one("#btn-generate", Button).disabled = False

    def action_generate(self) -> None:
        btn = self.query_one("#btn-generate", Button)
        if not btn.disabled:
            self.generate_payload()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-generate":
            self.action_generate()

    def action_reload(self) -> None:
        self.last_clipboard = ""
        self.query_one("#status-label", Label).update("[bold yellow]Reloading clipboard...[/bold yellow]")
        self.polling_timer.resume()
        self.check_clipboard()

    def generate_payload(self) -> None:
        if not self.payload:
            return
            
        prompt = self.payload.get("prompt", "")
        original = self.payload.get("original_request", "")
        files = self.payload.get("files", [])
        
        combined_prompt = []
        if original:
            combined_prompt.append(f"USER ORIGINAL REQUEST:\n{original}")
        if prompt:
            combined_prompt.append(f"ORCHESTRATOR ARCHITECTURE & INSTRUCTIONS:\n{prompt}")
        final_prompt_text = "\n\n".join(combined_prompt)
        
        buffer = []
        buffer.append("--- USER REQUEST ---")
        buffer.append(final_prompt_text)
        buffer.append("\n--- SYSTEM INSTRUCTIONS ---")
        
        sys_prompt = DEFAULT_SYSTEM_PROMPT_TEMPLATE.replace('{FILE_CULLING_INSTRUCTION}\n', '').replace('{FILE_CULLING_INSTRUCTION}', '')
        buffer.append(sys_prompt)
        buffer.append("\n--- USER REQUEST ---")
        buffer.append(final_prompt_text)
        buffer.append("\n--- FILE CONTEXT ---")
        
        separator = "-" * 35
        for file_path in files:
            full_path = os.path.join(self.root_dir, file_path)
            buffer.append(separator)
            buffer.append(f"FILE: {file_path}")
            buffer.append(separator)
            _, ext = os.path.splitext(file_path)
            lang = ext.lstrip('.').lower()
            buffer.append(f"```{lang}")
            
            if os.path.exists(full_path):
                try:
                    buffer.append(safe_read_file(full_path))
                except Exception as e:
                    buffer.append(f"[Error reading file: {e}]")
            else:
                buffer.append(f"[File not found: {file_path}]")
            buffer.append("```")
            buffer.append("")

        buffer.append("--- USER REQUEST (Reminder) ---")
        buffer.append(final_prompt_text)
        buffer.append("\n--- SYSTEM REMINDER ---")
        buffer.append("CRITICAL: You must ALWAYS start in PLANNING mode.")
        buffer.append("Do NOT output EXECUTION or ORCHESTRATION JSON yet.")
        buffer.append("When you enter EXECUTION or ORCHESTRATION mode, you MUST wrap the JSON output in a markdown code block (```json).")
        buffer.append("Create an implementation plan and wait for the user to review and approve it.")
        buffer.append("When in EXECUTION mode, your commit message in the JSON payload MUST strictly adhere to this exact multi-line template structure:")
        buffer.append("type(scope) : description")
        buffer.append("extra desc")
        buffer.append(" extra desc")
        buffer.append("")
            
        final_text = "\n".join(buffer)
        if getattr(self, 'use_file_clipboard', False):
            try:
                fd, temp_path = tempfile.mkstemp(prefix="combineCopy_prompt_", suffix=".txt", text=True)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(final_text)
                if copy_file_to_clipboard(temp_path):
                    self.notify("Payload saved to file and copied to clipboard!", title="Success")
                    self.exit(True)
            except Exception as e:
                self.notify(f"Failed to copy file: {e}", severity="error")
        else:
            if copy_to_clipboard(final_text):
                self.notify("Downstream payload copied to clipboard!", title="Success")
                self.exit(True)

class AutoAgentApp(App):
    """TUI for monitoring clipboard and applying AI execution changes."""
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #layout { height: 100%; }
    #sidebar {
        width: 30%;
        border-right: solid #5a4d45;
        background: #241f1c;
    }
    #sidebar ListView { height: 1fr; }
    #sidebar ListItem { height: auto; }
    #main-area {
        width: 70%;
        padding: 1 2;
    }
    .panel-title {
        background: #4a3f39;
        color: #d08c60;
        text-align: center;
        padding: 1; 
        text-style: bold;
    }
    .action-row {
        height: 3; 
        margin-bottom: 1;
        align: right middle;
    }
    Button { margin-left: 1; }
    #diff-view {
        margin-top: 1;
        height: 1fr;
        border: solid #5a4d45;
        background: #241f1c;
    }
    #diff-view:focus {
        border: double #d08c60;
    }
    #ai-markdown {
        height: auto;
        max-height: 40%;
        overflow-y: auto;
        border-bottom: solid #5a4d45;
    }
    #file-header {
        background: #3c3431;
        color: #ead6c9;
        padding: 0 1;
        height: auto;
        border-bottom: solid #5a4d45;
        margin-bottom: 1;
    }
    """
    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("a", "apply_file", "Apply File"),
        Binding("p", "partial_add", "Partial Add"),
        Binding("A", "apply_all", "Apply All"),
        Binding("c", "commit", "Commit"),
        Binding("d", "discard_file", "Discard File"),
        Binding("D", "discard_all", "Discard All"),
        Binding("r", "reload", "Reload Clipboard"),
        Binding("e", "copy_error", "Copy Error"),
        Binding("h", "human_correct", "Human Correct"),
        Binding("m", "open_meld", "Open in Meld"),
        Binding("f", "fix_json", "Fix JSON"),
    ]
    TITLE = "CombineCopy — Auto Agent Listener"

    def __init__(self, root_dir: str, known_files: list[str] | None = None, revert_mode: bool = False, ignore_initial_clipboard: bool = False, web_mode: bool = False):
        super().__init__()
        self.root_dir = root_dir
        self.known_files = known_files or []
        self.revert_mode = revert_mode
        self.web_mode = web_mode
        self.ignore_initial_clipboard = ignore_initial_clipboard
        self.last_clipboard = ""
        self.payload = None
        self.polling_timer = None
        self.json_error_text = None
        self.broken_json_content = ""
        if self.revert_mode:
            self.title = "CombineCopy — Auto Agent Listener (REVERT MODE)"
        if self.web_mode:
            self.title = "CombineCopy — Auto Agent Listener (WEB MACRO MODE)"

    def action_reload(self) -> None:
        self.last_clipboard = ""
        self.query_one("#status-label", Label).update("[bold yellow]Reloading clipboard...[/bold yellow]")
        self.check_clipboard()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Label("Waiting for AI...", id="status-label", classes="panel-title")
                yield ListView(id="file-list")
            with Vertical(id="main-area"):
                yield Label("Select a file to inspect changes", id="file-header")
                with Horizontal(classes="action-row", id="global-action-bar"):
                    yield Button("Apply All (Shift+A)", id="btn-apply-all", variant="success", disabled=True)
                    yield Button("Discard All (Shift+D)", id="btn-discard-all", variant="error", disabled=True)
                    yield Button("Commit (c)", id="btn-commit", variant="primary", disabled=True)
                    yield Button("Fix JSON (f)", id="btn-fix-json", variant="warning", disabled=True)
                with Horizontal(classes="action-row", id="file-action-bar"):
                    yield Button("Apply File (a)", id="btn-apply-file", variant="success", disabled=True)
                    yield Button("Partial Add (p)", id="btn-partial-add", variant="warning", disabled=True)
                    yield Button("Discard File (d)", id="btn-discard-file", variant="error", disabled=True)
                    yield Button("Human Correct (h)", id="btn-human-correct", variant="warning", disabled=True)
                    yield Button("Meld Diff (m)", id="btn-open-meld", variant="primary", disabled=True)
                yield Markdown("*(AI output will appear here)*", id="ai-markdown")
                yield RichLog(id="diff-view", highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        if self.ignore_initial_clipboard:
            try:
                self.last_clipboard = pyperclip.paste().strip()
            except Exception:
                pass
        self.polling_timer = self.set_interval(0.5, self.check_clipboard)
        self.query_one("#diff-view", RichLog).write("Select a file to view diffs.")

    def _extract_json_objects(self, text: str) -> list[str]:
        results = []
        idx = 0
        while idx < len(text):
            start_idx = text.find('{', idx)
            if start_idx == -1:
                break
            depth = 0
            in_string = False
            escape_next = False
            end_idx = -1
            for i in range(start_idx, len(text)):
                char = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break
            if end_idx != -1:
                results.append(text[start_idx:end_idx + 1])
                idx = end_idx + 1
            else:
                idx = start_idx + 1
        return results

    def check_clipboard(self) -> None:
        try:
            content = pyperclip.paste().strip()
            if not content or content == self.last_clipboard:
                return
            self.last_clipboard = content
            
            if '"phase":' in content and '"EXECUTION"' in content:
                json_blocks = self._extract_json_objects(content)
                for json_str in json_blocks:
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and data.get("phase") == "EXECUTION" and "files" in data:
                            self.load_payload(data)
                            return
                    except json.JSONDecodeError as e:
                        fixed_data, fixed_str = intelligent_json_fix(json_str)
                        if fixed_data and isinstance(fixed_data, dict) and fixed_data.get("phase") == "EXECUTION" and "files" in fixed_data:
                            self.notify("Intelligently auto-fixed JSON syntax errors!", title="Auto-Fix Success", severity="info")
                            self.load_payload(fixed_data)
                            return
                        if '"EXECUTION"' in json_str and '"phase"' in json_str:
                            self.show_json_error(e, json_str)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _normalize_text(self, text: str) -> str:
        return "\n".join(
            line.strip()
            for line in text.strip().split('\n')
            if line.strip()
        )

    def show_json_error(self, error: json.JSONDecodeError, content: str) -> None:
        self.polling_timer.pause()
        self.broken_json_content = content
        self.query_one("#status-label", Label).update("[bold red]JSON Parse Error[/bold red]")
        lines = content.split("\n")
        error_line = lines[error.lineno - 1] if 0 < error.lineno <= len(lines) else ""
        self.json_error_text = (
            f"Your execution JSON failed to parse due to a syntax error:\n"
            f"{error.msg} on line {error.lineno}, column {error.colno}\n\n"
            f"Failing line context:\n"
            f"{error_line}"
        )
        error_md = (
            f"### ❌ Invalid JSON from AI\n\n"
            f"**Option 1 (Recommended):** Press **'f'** to instantly open and fix the JSON locally.\n"
            f"**Option 2:** Press **'e'** to copy the error below and send it to the LLM to fix.\n\n"
            f"```text\n"
            f"{self.json_error_text}\n"
            f"```"
        )
        self.query_one("#ai-markdown", Markdown).update(error_md)
        self.query_one("#file-list", ListView).clear()
        
        diff_view = self.query_one("#diff-view", RichLog)
        diff_view.clear()
        diff_view.write(f"JSON Exception: {error.msg}\nLine: {error.lineno}\nColumn: {error.colno}\n\nContext:\n{error_line}")
        self._disable_all_buttons()
        self.query_one("#btn-discard-all", Button).disabled = False
        if self.query("Button#btn-fix-json"):
            self.query_one("#btn-fix-json", Button).disabled = False

    def _find_partial_matches(self, search_text: str, file_text: str) -> list:
        search_lines = search_text.splitlines()
        file_lines = file_text.splitlines()
        if len(search_lines) <= 1: return []
        search_norm = [line.strip() for line in search_lines]
        file_norm = [line.strip() for line in file_lines]
        
        matcher = difflib.SequenceMatcher(None, search_norm, file_norm)
        blocks = matcher.get_matching_blocks()
        best_n = 0
        candidates = []
        for block in blocks:
            if block.size > 0:
                matched_text = "".join(search_norm[block.a : block.a + block.size])
                if not matched_text: continue
                if block.size > best_n:
                    best_n = block.size
                    candidates = [{"start_line": block.b + 1, "end_line": block.b + block.size}]
                elif block.size == best_n:
                    candidates.append({"start_line": block.b + 1, "end_line": block.b + block.size})
        if best_n > 0:
            unique_cands = {}
            for c in candidates:
                unique_cands[c["start_line"]] = c
            return sorted(unique_cands.values(), key=lambda x: x["start_line"])
        return []

    def _validate_file_obj(self, file_obj: dict) -> None:
        action = file_obj.get("action", "modify").upper()
        path = file_obj.get("path", "unknown")
        full_path = os.path.join(self.root_dir, path)
        errors = []
        if "_revert_error" in file_obj:
            errors.append(file_obj["_revert_error"])
        if not os.path.exists(full_path) and action != "CREATE":
            filename = os.path.basename(path)
            if self.known_files:
                matches = [f for f in self.known_files if os.path.basename(f) == filename]
                if len(matches) == 1:
                    correct_path_rel = os.path.relpath(matches[0], self.root_dir)
                    warn_msg = f"Path corrected from '{path}' to '{correct_path_rel}'."
                    if warn_msg not in file_obj.setdefault("_warnings", []):
                        file_obj["_warnings"].append(warn_msg)
                    file_obj["path"] = correct_path_rel
                    path = correct_path_rel
                    full_path = os.path.join(self.root_dir, path)
                elif len(matches) > 1:
                    if self.web_mode:
                        file_obj.setdefault("_warnings", []).append(f"Ambiguous file: '{filename}' found in multiple locations.")
                    else:
                        errors.append(f"Ambiguous file: '{filename}' found in multiple locations.")
                else:
                    if self.web_mode:
                        file_obj.setdefault("_warnings", []).append(f"Target file '{path}' does not exist locally.")
                    else:
                        errors.append(f"Target file '{path}' does not exist and was not found in context.")
            else:
                if self.web_mode:
                    file_obj.setdefault("_warnings", []).append(f"Target file '{path}' does not exist locally.")
                else:
                    errors.append(f"Target file '{path}' does not exist.")

        if action == "MODIFY" and not errors:
            if "regex_replace" in file_obj and os.path.exists(full_path):
                old_text = safe_read_file(full_path)
                for b_idx, block in enumerate(file_obj.get("regex_replace", [])):
                    pattern = block.get("pattern", "")
                    if pattern:
                        try:
                            compiled = re.compile(pattern)
                            if not compiled.search(old_text):
                                warn_msg = f"Regex pattern '{pattern}' found no matches."
                                if warn_msg not in file_obj.setdefault("_warnings", []):
                                    file_obj["_warnings"].append(warn_msg)
                        except re.error as e:
                            errors.append(f"Invalid regex pattern '{pattern}': {e}")

            if "search_replace" in file_obj and os.path.exists(full_path):
                try:
                    old_text = safe_read_file(full_path)
                    for b_idx, block in enumerate(file_obj.get("search_replace", [])):
                        block.pop("_candidates", None)
                        if "replace" not in block:
                            errors.append(f"No replacement found for search block {b_idx + 1}.")
                        search_text = block.get("search", "")
                        if search_text and search_text not in old_text:
                            normalized_old = self._normalize_text(old_text)
                            normalized_search = self._normalize_text(search_text)
                            if normalized_search in normalized_old:
                                search_lines = search_text.strip('\n').split('\n')
                                source_lines = old_text.split('\n')
                                found_exact = False
                                for i in range(len(source_lines) - len(search_lines) + 1):
                                    window = '\n'.join(source_lines[i : i + len(search_lines)])
                                    if self._normalize_text(window) == normalized_search:
                                        block['search'] = window
                                        warn_msg = f"Used fuzzy matching for search block {b_idx + 1}."
                                        if warn_msg not in file_obj.setdefault("_warnings", []):
                                            file_obj["_warnings"].append(warn_msg)
                                        found_exact = True
                                        break
                                if not found_exact:
                                    errors.append(f"Fuzzy match found but couldn't map to original text for block {b_idx+1}.")
                            else:
                                candidates = self._find_partial_matches(search_text, old_text)
                                if candidates:
                                    block["_candidates"] = candidates
                                    block["_original_search"] = search_text
                                    errors.append(f"Search block {b_idx + 1} not found. Found {len(candidates)} partial match(es). Press 'h' to resolve.")
                                else:
                                    errors.append(f"Search block {b_idx + 1} not found. Fuzzy match and partial match also failed.")
                except Exception as e:
                    errors.append(f"Error reading file: {e}")
        file_obj["_errors"] = errors

    def load_payload(self, data: dict) -> None:
        self.polling_timer.pause()
        for file_obj in data.get("files", []):
            for block in file_obj.get("search_replace", []):
                if "replacement" in block and "replace" not in block:
                    block["replace"] = block.pop("replacement")

        if getattr(self, 'revert_mode', False):
            data["commit_message"] = "Revert: " + data.get("commit_message", "")
            for file_obj in data.get("files", []):
                action = file_obj.get("action", "modify").lower()
                if action == "create":
                    file_obj["action"] = "delete"
                elif action == "delete":
                    file_obj["action"] = "create"
                    file_obj["content"] = ""
                    file_obj["_revert_warning"] = "Reverting a delete will create an empty file."
                elif action == "modify":
                    if "search_replace" in file_obj:
                        new_sr = []
                        for block in reversed(file_obj.get("search_replace", [])):
                            new_sr.append({
                                "search": block.get("replace", ""),
                                "replace": block.get("search", "")
                            })
                        file_obj["search_replace"] = new_sr
                    elif "content" in file_obj:
                        file_obj["_revert_error"] = "Cannot revert a full file overwrite without original content."
        self.payload = data
        self.query_one("#status-label", Label).update("Files waiting to be changed")
        self.query_one("#ai-markdown", Markdown).update(data.get("markdown", "No markdown provided."))
        
        for idx, file_obj in enumerate(data.get("files", [])):
            file_obj["_status"] = "pending"
            file_obj.setdefault("_warnings", [])
            if "_revert_warning" in file_obj:
                file_obj["_warnings"].append(file_obj.pop("_revert_warning"))
            self._validate_file_obj(file_obj)
        self.refresh_file_list()
        file_list = self.query_one("#file-list", ListView)
        if len(file_list) > 0:
            file_list.index = 0

    def _disable_all_buttons(self) -> None:
        self.query_one("#btn-apply-all", Button).disabled = True
        self.query_one("#btn-discard-all", Button).disabled = True
        self.query_one("#btn-commit", Button).disabled = True
        self.query_one("#btn-apply-file", Button).disabled = True
        self.query_one("#btn-discard-file", Button).disabled = True
        if self.query("Button#btn-partial-add"):
            self.query_one("#btn-partial-add", Button).disabled = True
        if self.query("Button#btn-human-correct"):
            self.query_one("#btn-human-correct", Button).disabled = True
        if self.query("Button#btn-open-meld"):
            self.query_one("#btn-open-meld", Button).disabled = True
        if self.query("Button#btn-fix-json"):
            self.query_one("#btn-fix-json", Button).disabled = True

    def refresh_file_list(self) -> None:
        if not self.payload: return
        file_list = self.query_one("#file-list", ListView)
        current_idx = file_list.index
        file_list.clear()
        for idx, file_obj in enumerate(self.payload.get("files", [])):
            action = file_obj.get("action", "modify").upper()
            path = file_obj.get("path", "unknown")
            status = file_obj.get("_status", "pending")
            errors = file_obj.get("_errors", [])
            warnings = file_obj.get("_warnings", [])
            if status == "applied":
                status_marker = " [bold green]✓[/bold green]"
                style = "dim"
            elif status == "discarded":
                status_marker = " [bold red]✗[/bold red]"
                style = "strike dim"
            else:
                status_marker = ""
                style = ""
            color = "green" if action == "CREATE" else "yellow" if action == "MODIFY" else "red"
            err_marker = " [bold red](Error)[/bold red]" if errors else ""
            warn_marker = ""
            if "Path corrected" in "".join(warnings):
                warn_marker += " [yellow](Path Corrected)[/yellow]"
            if any("fuzzy matching" in w for w in warnings):
                warn_marker += " [yellow](Fuzzy Match)[/yellow]"
            if any("Human corrected" in w for w in warnings):
                warn_marker += " [yellow](Human Corrected)[/yellow]"
            label_text = f"[{color}]{action}[/{color}] {path}{err_marker}{warn_marker}{status_marker}"
            unique_id = f"file-{idx}-{time.time_ns()}"
            item = ListItem(Label(label_text, classes=style), id=unique_id)
            file_list.append(item)
        if current_idx is not None and current_idx < len(file_list):
            file_list.index = current_idx
            self._render_diff_for_index(current_idx)
        self._update_buttons()

    def _update_buttons(self) -> None:
        if not self.payload:
            self._disable_all_buttons()
            return
        files = self.payload.get("files", [])
        has_pending = any(f.get("_status") == "pending" for f in files)
        has_applied = any(f.get("_status") == "applied" for f in files)
        self.query_one("#btn-apply-all", Button).disabled = not has_pending
        self.query_one("#btn-discard-all", Button).disabled = not has_pending
        self.query_one("#btn-commit", Button).disabled = not has_applied
        
        file_list = self.query_one("#file-list", ListView)
        if file_list.index is not None and file_list.index < len(files):
            selected_file = files[file_list.index]
            is_pending = selected_file.get("_status") == "pending"
            self.query_one("#btn-apply-file", Button).disabled = not is_pending
            self.query_one("#btn-partial-add", Button).disabled = not is_pending
            self.query_one("#btn-discard-file", Button).disabled = not is_pending
            self.query_one("#btn-open-meld", Button).disabled = not is_pending
            has_candidates = False
            for block in selected_file.get("search_replace", []):
                if "_candidates" in block:
                    has_candidates = True
                    break
            self.query_one("#btn-human-correct", Button).disabled = not (is_pending and has_candidates)
        else:
            self.query_one("#btn-apply-file", Button).disabled = True
            if self.query("Button#btn-partial-add"):
                self.query_one("#btn-partial-add", Button).disabled = True
            self.query_one("#btn-discard-file", Button).disabled = True
            self.query_one("#btn-human-correct", Button).disabled = True
            self.query_one("#btn-open-meld", Button).disabled = True

    def _compute_new_text(self, file_obj: dict, old_text: str) -> str:
        if "content" in file_obj:
            return file_obj["content"]
        new_text = old_text
        for block in file_obj.get("search_replace", []):
            search = block.get("search", "")
            replace = block.get("replace", "")
            if search and search in new_text:
                new_text = new_text.replace(search, replace, 1)
        for block in file_obj.get("regex_replace", []):
            pattern = block.get("pattern", "")
            replacement = block.get("replacement", "")
            if pattern:
                try:
                    new_text = re.sub(pattern, replacement, new_text)
                except re.error:
                    pass
        return new_text

    def _render_diff_for_index(self, idx: int) -> None:
        if not self.payload or idx < 0 or idx >= len(self.payload.get("files", [])): return
        self._update_buttons()
        file_obj = self.payload["files"][idx]
        path = file_obj.get("path")
        dirname = os.path.dirname(path)
        filename = os.path.basename(path)
        header_text = Text()
        header_text.append("Target: ", style="bold cyan")
        if dirname:
            header_text.append(f"{dirname}/", style="dim")
        header_text.append(filename, style="bold yellow")
        self.query_one("#file-header", Label).update(header_text)
        
        full_path = os.path.join(self.root_dir, path)
        old_text = ""
        if os.path.exists(full_path):
            try:
                old_text = safe_read_file(full_path)
            except Exception:
                old_text = "[Error reading existing file]\n"
        new_text = self._compute_new_text(file_obj, old_text)
        diff_view = self.query_one("#diff-view", RichLog)
        diff_view.clear()
        
        header_text = ""
        errors = file_obj.get("_errors", [])
        warnings = file_obj.get("_warnings", [])
        if warnings:
            warn_header = f"⚠️ AUTOMATED CORRECTIONS APPLIED FOR {file_obj.get('path', 'unknown')}\n"
            for warn in warnings:
                warn_header += f" - {warn}\n"
            warn_header += "=" * 60 + "\n\n"
            header_text += warn_header
        if errors:
            error_header = f"⛔️ ACTION FAILED VALIDATION FOR {file_obj.get('path', 'unknown')}\n"
            for err in errors:
                error_header += f" - {err}\n"
            error_header += "\nCopy this error and give it to the AI to correct its search block.\n"
            error_header += "=" * 60 + "\n\n"
            header_text += error_header
        if header_text:
            style = "bold red" if errors else "bold yellow"
            diff_view.write(Text(header_text, style=style))
        if old_text == new_text:
            diff_view.write(Text("No changes detected.", style="dim"))
            return
        render_word_diff(old_text, new_text, diff_view)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id and event.item.id.startswith("file-"):
            idx = int(event.item.id.split("-")[1])
            self._render_diff_for_index(idx)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None and event.item.id and event.item.id.startswith("file-"):
            idx = int(event.item.id.split("-")[1])
            self._render_diff_for_index(idx)

    def _check_auto_reset(self) -> None:
        files = self.payload.get("files", [])
        has_pending = any(f.get("_status") == "pending" for f in files)
        has_applied = any(f.get("_status") == "applied" for f in files)
        if not has_pending and not has_applied:
            self.reset_state()

    def action_apply_file(self) -> None:
        btn = self.query_one("#btn-apply-file", Button)
        if not btn.disabled:
            file_list = self.query_one("#file-list", ListView)
            if file_list.index is not None:
                if self.web_mode:
                    self.app.push_screen(MacroScreen(self.payload, [file_list.index]), self.on_macro_done)
                else:
                    self._apply_single_file(file_list.index)
                    self.refresh_file_list()

    def action_partial_add(self) -> None:
        btn = self.query_one("#btn-partial-add", Button)
        if not btn.disabled and self.payload:
            file_list = self.query_one("#file-list", ListView)
            if file_list.index is not None:
                file_idx = file_list.index
                file_obj = self.payload["files"][file_idx]
                if file_obj.get("action", "").lower() != "modify":
                    self.notify("Partial Add is only available for modified files.", severity="warning")
                    return
                full_path = os.path.join(self.root_dir, file_obj["path"])
                old_text = safe_read_file(full_path) if os.path.exists(full_path) else ""
                new_text = self._compute_new_text(file_obj, old_text)
                if old_text == new_text:
                    self.notify("No changes detected to partially add.", severity="warning")
                    return
                self.app.push_screen(
                    PartialAddScreen(file_obj["path"], old_text, new_text),
                    callback=lambda result: self.on_partial_add_result(file_idx, result)
                )

    def on_partial_add_result(self, file_idx: int, resolved_text: str | None) -> None:
        if resolved_text is None: return
        file_obj = self.payload["files"][file_idx]
        file_obj["content"] = resolved_text
        file_obj.pop("search_replace", None)
        file_obj.pop("regex_replace", None)
        self._apply_single_file(file_idx)
        self.refresh_file_list()
        self._check_auto_reset()

    def action_discard_file(self) -> None:
        btn = self.query_one("#btn-discard-file", Button)
        if not btn.disabled:
            file_list = self.query_one("#file-list", ListView)
            if file_list.index is not None:
                self.payload["files"][file_list.index]["_status"] = "discarded"
                self.refresh_file_list()
                self._check_auto_reset()

    def action_apply_all(self) -> None:
        btn = self.query_one("#btn-apply-all", Button)
        if not btn.disabled:
            pending_indices = [i for i, f in enumerate(self.payload["files"]) if f.get("_status") == "pending"]
            if self.web_mode:
                self.app.push_screen(MacroScreen(self.payload, pending_indices), self.on_macro_done)
            else:
                for i in pending_indices:
                    self._apply_single_file(i)
                self.refresh_file_list()

    def on_macro_done(self, completed_indices: list[int] | None) -> None:
        if completed_indices:
            for idx in completed_indices:
                self.payload["files"][idx]["_status"] = "applied"
            self.refresh_file_list()
            self._check_auto_reset()

    def action_discard_all(self) -> None:
        btn = self.query_one("#btn-discard-all", Button)
        if not btn.disabled:
            for f in self.payload["files"]:
                if f.get("_status") == "pending":
                    f["_status"] = "discarded"
            self.refresh_file_list()
            self._check_auto_reset()

    def action_commit(self) -> None:
        btn = self.query_one("#btn-commit", Button)
        if not btn.disabled:
            self.commit_changes()
            self.reset_state()

    def action_human_correct(self) -> None:
        file_list = self.query_one("#file-list", ListView)
        if file_list.index is not None and self.payload:
            file_obj = self.payload["files"][file_list.index]
            for b_idx, block in enumerate(file_obj.get("search_replace", [])):
                if "_candidates" in block:
                    full_path = os.path.join(self.root_dir, file_obj["path"])
                    old_text = safe_read_file(full_path)
                    self.app.push_screen(
                        HumanCorrectScreen(
                            file_path=file_obj["path"],
                            file_text=old_text,
                            original_search=block["_original_search"],
                            candidates=block["_candidates"],
                            replace_text=block.get("replace", "")
                        ),
                        callback=lambda selected_text, b=b_idx: self.on_human_correct_result(file_list.index, b, selected_text)
                    )
                    return
            self.notify("No fixable blocks found in this file.", severity="warning")

    def on_human_correct_result(self, file_idx: int, block_idx: int, selected_text: str | None) -> None:
        if selected_text is None: return
        file_obj = self.payload["files"][file_idx]
        block = file_obj["search_replace"][block_idx]
        block["search"] = selected_text
        block.pop("_candidates", None)
        block.pop("_original_search", None)
        if "_warnings" not in file_obj:
            file_obj["_warnings"] = []
        file_obj["_warnings"].append(f"Human corrected search block {block_idx + 1}.")
        self._validate_file_obj(file_obj)
        self.refresh_file_list()

    def action_fix_json(self) -> None:
        btn = self.query_one("#btn-fix-json", Button)
        if btn.disabled or not hasattr(self, 'broken_json_content') or not self.broken_json_content:
            return
        btn.disabled = True
        thread = threading.Thread(target=self._fix_json_worker, args=(self.broken_json_content,), daemon=True)
        thread.start()
        self.notify("Waiting for external editor to close...", severity="info")
        
    def _fix_json_worker(self, current_text: str) -> None:
        fd, temp_path = tempfile.mkstemp(suffix=".json", text=True)
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
            self.app.call_from_thread(self.notify, f"Editor failed to launch: {e}", severity="error")
        try:
            with open(temp_path, 'r', encoding='utf-8') as f:
                new_text = f.read()
            if new_text != current_text:
                pyperclip.copy(new_text)
                self.app.call_from_thread(self.notify, "Clipboard updated with fixed JSON!", title="Success")
                self.app.call_from_thread(self.action_reload)
        except Exception as e:
            self.app.call_from_thread(self.notify, f"Failed to read from editor: {e}", severity="error")
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            def re_enable():
                if self.query("Button#btn-fix-json"):
                    btn = self.query_one("#btn-fix-json", Button)
                    if hasattr(self, 'broken_json_content') and self.broken_json_content == current_text:
                        btn.disabled = False
            self.app.call_from_thread(re_enable)

    def action_copy_error(self) -> None:
        if self.json_error_text:
            pyperclip.copy(self.json_error_text)
            self.notify("JSON error copied to clipboard!", title="Copied")
            return
        if not self.payload: return
        file_list = self.query_one("#file-list", ListView)
        if file_list.index is not None and file_list.index < len(self.payload["files"]):
            file_obj = self.payload["files"][file_list.index]
            errors = file_obj.get("_errors", [])
            if errors:
                error_text = f"ACTION FAILED VALIDATION FOR {file_obj.get('path', 'unknown')}\n"
                for err in errors:
                    error_text += f" - {err}\n"
                pyperclip.copy(error_text)
                self.notify("File validation error copied!", title="Copied")
            else:
                self.notify("No errors for the selected file.", severity="warning")

    def action_open_meld(self) -> None:
        btn = self.query_one("#btn-open-meld", Button)
        if btn.disabled: return
        file_list = self.query_one("#file-list", ListView)
        if file_list.index is not None and self.payload:
            file_obj = self.payload["files"][file_list.index]
            path = file_obj.get("path")
            full_path = os.path.join(self.root_dir, path)
            old_text = ""
            if os.path.exists(full_path):
                old_text = safe_read_file(full_path)
            new_text = self._compute_new_text(file_obj, old_text)
            fd_old, path_old = tempfile.mkstemp(suffix="_old_" + os.path.basename(path))
            fd_new, path_new = tempfile.mkstemp(suffix="_new_" + os.path.basename(path))
            with os.fdopen(fd_old, 'w', encoding='utf-8') as f:
                f.write(old_text)
            with os.fdopen(fd_new, 'w', encoding='utf-8') as f:
                f.write(new_text)
            try:
                subprocess.Popen(["meld", path_old, path_new])
                self.notify("Opened file in Meld.", severity="info")
            except FileNotFoundError:
                try:
                    subprocess.Popen(["meld.exe", path_old, path_new])
                    self.notify("Opened file in Meld.", severity="info")
                except FileNotFoundError:
                    self.notify("Meld not found. Please ensure 'meld' or 'meld.exe' is in your PATH.", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-discard-all":
            self.action_discard_all()
        elif btn_id == "btn-apply-all":
            self.action_apply_all()
        elif btn_id == "btn-discard-file":
            self.action_discard_file()
        elif btn_id == "btn-apply-file":
            self.action_apply_file()
        elif btn_id == "btn-partial-add":
            self.action_partial_add()
        elif btn_id == "btn-commit":
            self.action_commit()
        elif btn_id == "btn-human-correct":
            self.action_human_correct()
        elif btn_id == "btn-open-meld":
            self.action_open_meld()
        elif btn_id == "btn-fix-json":
            self.action_fix_json()

    def _apply_single_file(self, idx: int) -> None:
        file_obj = self.payload["files"][idx]
        action = file_obj.get("action", "").lower()
        path = file_obj.get("path")
        full_path = os.path.join(self.root_dir, path)
        if action == "delete":
            if os.path.exists(full_path):
                os.remove(full_path)
            file_obj["_status"] = "applied"
            return
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if action == "create":
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(file_obj.get("content", ""))
        elif action == "modify":
            old_text = ""
            if os.path.exists(full_path):
                old_text = safe_read_file(full_path)
            new_text = self._compute_new_text(file_obj, old_text)
            old_lines = old_text.splitlines(keepends=True)
            new_lines = new_text.splitlines(keepends=True)
            diff = difflib.unified_diff(old_lines, new_lines, n=0)
            added = 0
            removed = 0
            for line in diff:
                if line.startswith('+') and not line.startswith('+++'):
                    added += 1
                elif line.startswith('-') and not line.startswith('---'):
                    removed += 1
            file_obj["_added"] = added
            file_obj["_removed"] = removed
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_text)
        file_obj["_status"] = "applied"

    def commit_changes(self) -> None:
        msg = self.payload.get("commit_message", "Auto-commit from AI agent")
        try:
            subprocess.run(["git", "add", "."], cwd=self.root_dir, check=True)
            subprocess.run(["git", "commit", "-m", msg], cwd=self.root_dir, check=True)
            self.notify("Changes successfully committed to Git! Closing app.", title="Success")
            summary_data = {
                "commit_message": msg,
                "files": self.payload.get("files", [])
            }
            self.exit(summary_data)
        except subprocess.CalledProcessError as e:
            self.notify(f"Git error: {e}", title="Error", severity="error")

    def reset_state(self) -> None:
        self.payload = None
        self.last_clipboard = ""
        self.json_error_text = None
        self.broken_json_content = ""
        self.query_one("#status-label", Label).update("Waiting for AI...")
        self.query_one("#ai-markdown", Markdown).update("*(AI output will appear here)*")
        self.query_one("#file-list", ListView).clear()
        
        diff_view = self.query_one("#diff-view", RichLog)
        diff_view.clear()
        diff_view.write("Select a file to view diffs.")
        self._disable_all_buttons()
        pyperclip.copy("")
        self.polling_timer.resume()
