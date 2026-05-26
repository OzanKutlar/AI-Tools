import os
import subprocess
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Label, ListView, ListItem, TextArea, Button, Input, Markdown
from textual.binding import Binding
from textual.screen import ModalScreen

from kanban_store import load_tasks, save_tasks, generate_id
from cc_utils import copy_to_clipboard, safe_read_file, get_files_recursive, generate_tree_string
from cc_prompts import DEFAULT_SYSTEM_PROMPT_TEMPLATE
from tui_selection import run_file_selector
from tui_apply import run_auto_agent

class TaskCreateModal(ModalScreen[dict]):
    CSS = """
    TaskCreateModal { align: center middle; background: rgba(0, 0, 0, 0.8); }
    #tc-dialog { width: 60%; height: 60%; border: solid #d08c60; background: #2d2825; padding: 1 2; }
    .tc-title { text-align: center; text-style: bold; color: #d08c60; margin-bottom: 1; }
    Input { margin-bottom: 1; }
    TextArea { height: 1fr; margin-bottom: 1; }
    #tc-buttons { height: 3; align: right middle; }
    Button { margin-left: 1; }
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="tc-dialog"):
            yield Label("Create New Task", classes="tc-title")
            yield Input(id="tc-title-input", placeholder="Task Title (e.g. Add user auth)")
            yield Label("Description & Prompt:")
            yield TextArea(id="tc-desc-input")
            with Horizontal(id="tc-buttons"):
                yield Button("Save Task", id="btn-save", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            title = self.query_one("#tc-title-input", Input).value.strip()
            desc = self.query_one("#tc-desc-input", TextArea).text.strip()
            if title and desc:
                self.dismiss({"title": title, "description": desc})
            else:
                self.app.notify("Title and Description are required.", severity="error")
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

class TaskRecallModal(ModalScreen[str]):
    CSS = """
    TaskRecallModal { align: center middle; background: rgba(0, 0, 0, 0.8); }
    #tr-dialog { width: 60%; height: 60%; border: solid #ff5555; background: #2d2825; padding: 1 2; }
    .tr-title { text-align: center; text-style: bold; color: #ff5555; margin-bottom: 1; }
    TextArea { height: 1fr; margin-bottom: 1; }
    #tr-buttons { height: 3; align: right middle; }
    Button { margin-left: 1; }
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="tr-dialog"):
            yield Label("Recall Task: Describe the Error", classes="tr-title")
            yield Label("What did the AI get wrong in the last commit? This will be sent back along with its last diff.")
            yield TextArea(id="tr-error-input")
            with Horizontal(id="tr-buttons"):
                yield Button("Submit Recall", id="btn-recall-save", variant="error")
                yield Button("Cancel", id="btn-recall-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-recall-save":
            err = self.query_one("#tr-error-input", TextArea).text.strip()
            if err:
                self.dismiss(err)
            else:
                self.app.notify("Please describe the error to recall the task.", severity="error")
        elif event.button.id == "btn-recall-cancel":
            self.dismiss(None)

class KanbanApp(App):
    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #main-layout { height: 70%; }
    .col-container { width: 25%; height: 100%; border-right: solid #5a4d45; padding: 1; }
    .col-title { text-align: center; text-style: bold; background: #4a3f39; color: #d08c60; margin-bottom: 1; padding: 1; }
    ListView { background: #241f1c; height: 1fr; }
    ListItem { padding: 1; }
    #details-pane { height: 30%; border-top: solid #d08c60; padding: 1 2; background: #1e1a18; }
    .details-header { text-style: bold; color: #ead6c9; margin-bottom: 1; }
    .details-info { color: #a0a0a0; }
    """
    BINDINGS = [
        Binding("c", "create_task", "Create Task"),
        Binding("s", "select_files", "Select Files"),
        Binding("g", "generate_run", "Generate & Run"),
        Binding("r", "recall_task", "Recall Task"),
        Binding("d", "delete_task", "Delete Task"),
        Binding("m", "move_right", "Move Right"),
        Binding("n", "move_left", "Move Left"),
        Binding("escape", "quit", "Quit")
    ]

    def __init__(self, root_dir: str):
        super().__init__()
        self.root_dir = root_dir
        self.tasks = load_tasks(root_dir)
        self.STATUSES = ["waiting", "selecting", "in_progress", "completed"]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            with Vertical(classes="col-container"):
                yield Label("WAITING", classes="col-title")
                yield ListView(id="list-waiting")
            with Vertical(classes="col-container"):
                yield Label("SELECTING", classes="col-title")
                yield ListView(id="list-selecting")
            with Vertical(classes="col-container"):
                yield Label("IN PROGRESS", classes="col-title")
                yield ListView(id="list-in_progress")
            with Vertical(classes="col-container"):
                yield Label("COMPLETED", classes="col-title")
                yield ListView(id="list-completed")
        with Vertical(id="details-pane"):
            yield Label("Select a task to view details", id="det-title", classes="details-header")
            yield Markdown("", id="det-desc")
            yield Label("", id="det-meta", classes="details-info")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_board()

    def refresh_board(self) -> None:
        for status in self.STATUSES:
            lv = self.query_one(f"#list-{status}", ListView)
            lv.clear()
            for t in self.tasks:
                if t.get("status") == status:
                    label = f"[{t['id']}] {t['title']}"
                    if t.get("recall_error"):
                        label = "[bold red]RECALLED[/bold red] " + label
                    lv.append(ListItem(Label(label), id=f"item-{t['id']}"))
        save_tasks(self.root_dir, self.tasks)

    def get_focused_task(self) -> dict | None:
        for status in self.STATUSES:
            lv = self.query_one(f"#list-{status}", ListView)
            if lv.has_focus and lv.highlighted_child:
                t_id = lv.highlighted_child.id.replace("item-", "")
                for t in self.tasks:
                    if t["id"] == t_id:
                        return t
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.update_details()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.update_details()

    def update_details(self) -> None:
        task = self.get_focused_task()
        if not task:
            self.query_one("#det-title", Label).update("Select a task to view details")
            self.query_one("#det-desc", Markdown).update("")
            self.query_one("#det-meta", Label).update("")
            return
        
        self.query_one("#det-title", Label).update(f"{task['id']} - {task['title']}")
        desc_text = task.get("description", "")
        if task.get("recall_error"):
            desc_text += f"\n\n**[RECALL ERROR]**\n{task['recall_error']}"
        self.query_one("#det-desc", Markdown).update(desc_text)
        
        files = task.get("files", [])
        meta = f"Status: {task['status'].upper()} | Attached Files: {len(files)}"
        if task.get("commit_hash"):
            meta += f" | Last Commit: {task['commit_hash'][:7]}"
        self.query_one("#det-meta", Label).update(meta)

    def action_create_task(self) -> None:
        def check_create(data: dict | None) -> None:
            if data:
                new_task = {
                    "id": generate_id(self.tasks),
                    "title": data["title"],
                    "description": data["description"],
                    "files": [],
                    "status": "waiting"
                }
                self.tasks.append(new_task)
                self.refresh_board()
        self.push_screen(TaskCreateModal(), check_create)

    def action_delete_task(self) -> None:
        task = self.get_focused_task()
        if task:
            self.tasks.remove(task)
            self.refresh_board()

    def action_move_right(self) -> None:
        task = self.get_focused_task()
        if task:
            idx = self.STATUSES.index(task["status"])
            if idx < len(self.STATUSES) - 1:
                task["status"] = self.STATUSES[idx + 1]
                self.refresh_board()

    def action_move_left(self) -> None:
        task = self.get_focused_task()
        if task:
            idx = self.STATUSES.index(task["status"])
            if idx > 0:
                task["status"] = self.STATUSES[idx - 1]
                self.refresh_board()

    def action_select_files(self) -> None:
        task = self.get_focused_task()
        if not task:
            self.notify("Highlight a task first.", severity="warning")
            return

        all_files = get_files_recursive(self.root_dir, 0, 10, None)
        
        with self.suspend():
            selected = run_file_selector(self.root_dir, all_files)
            
        if selected is not None:
            sel_paths, _ = selected
            rel_paths = [os.path.relpath(p, self.root_dir) for p in sel_paths]
            task["files"] = rel_paths
            if task["status"] == "waiting":
                task["status"] = "selecting"
            self.refresh_board()
            self.update_details()

    def action_recall_task(self) -> None:
        task = self.get_focused_task()
        if not task:
            return
        if task["status"] != "completed":
            self.notify("Only completed tasks can be recalled.", severity="warning")
            return
            
        def check_recall(err: str | None) -> None:
            if err:
                task["recall_error"] = err
                task["status"] = "in_progress"
                self.refresh_board()
                self.notify("Task recalled and moved back to In Progress.")
        self.push_screen(TaskRecallModal(), check_recall)

    def generate_task_prompt(self, task: dict) -> str:
        buffer = []
        buffer.append("--- USER REQUEST ---")
        buffer.append(f"TASK: {task.get('title', '')}")
        buffer.append(f"DESCRIPTION:\n{task.get('description', '')}")

        if task.get('recall_error'):
            buffer.append("\n--- TASK RECALL FEEDBACK ---")
            buffer.append("Your previous attempt at this task had issues. Please analyze the diff and correct the mistakes.")
            buffer.append(f"USER ERROR REPORT:\n{task.get('recall_error')}")
            
            if task.get('commit_hash'):
                try:
                    diff = subprocess.check_output(
                        ['git', 'show', task['commit_hash']], 
                        cwd=self.root_dir, 
                        text=True,
                        stderr=subprocess.STDOUT
                    )
                    buffer.append("\nPREVIOUS ATTEMPT DIFF:")
                    buffer.append(f"```diff\n{diff}\n```")
                except Exception as e:
                    buffer.append(f"[Could not retrieve diff for {task['commit_hash']}: {e}]")

        buffer.append("\n--- SYSTEM INSTRUCTIONS ---")
        buffer.append(DEFAULT_SYSTEM_PROMPT_TEMPLATE.replace('{FILE_CULLING_INSTRUCTION}\n', '').replace('{FILE_CULLING_INSTRUCTION}', ''))
        
        buffer.append("\n--- DIRECTORY AST MAP ---")
        all_files = get_files_recursive(self.root_dir, 0, 10, None)
        buffer.append(generate_tree_string(all_files, self.root_dir))

        buffer.append("\n--- FILE CONTEXT ---")
        separator = "-" * 35
        for fpath in task.get("files", []):
            full_path = os.path.join(self.root_dir, fpath)
            buffer.append(separator)
            buffer.append(f"FILE: {fpath}")
            buffer.append(separator)
            _, ext = os.path.splitext(fpath)
            lang = ext.lstrip('.').lower()
            buffer.append(f"```{lang}")
            if os.path.exists(full_path):
                try:
                    buffer.append(safe_read_file(full_path))
                except Exception as e:
                    buffer.append(f"[Error reading file: {e}]")
            else:
                buffer.append("[File not found]")
            buffer.append("```\n")

        buffer.append("--- USER REQUEST (Reminder) ---")
        buffer.append(f"TASK: {task.get('title', '')}")
        buffer.append("Please begin with your Implementation Plan and Task Checklist in PLANNING mode.")
        return "\n".join(buffer)

    def action_generate_run(self) -> None:
        task = self.get_focused_task()
        if not task:
            self.notify("Highlight a task first.", severity="warning")
            return
        if not task.get("files"):
            self.notify("Task has no attached files. Press 'S' to attach context.", severity="warning")
            return

        prompt_text = self.generate_task_prompt(task)
        if copy_to_clipboard(prompt_text):
            self.notify("Prompt copied to clipboard! Launching agent...", severity="info")
        
        if task["status"] in ["waiting", "selecting"]:
            task["status"] = "in_progress"
            self.refresh_board()

        with self.suspend():
            result = run_auto_agent(self.root_dir, revert_mode=False, web_mode=False, ignore_initial_clipboard=False)

        if result and "commit_message" in result:
            task["status"] = "completed"
            if "commit_hash" in result:
                task["commit_hash"] = result["commit_hash"]
            if "recall_error" in task:
                del task["recall_error"]
            self.refresh_board()
            self.notify("Task marked as Completed!", severity="success")
