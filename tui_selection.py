import os
import sys
import json
import tempfile
import asyncio
import subprocess
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Tree, Header, Footer, Label, Input
from textual.binding import Binding

class SelectionTree(Tree):
    """Custom Tree that prevents Enter from expanding nodes and maps vim keys."""
    BINDINGS = [
        Binding("enter", "toggle_select", "Toggle Selection"),
        Binding("space", "toggle_select", "Toggle Selection"),
        Binding("i", "toggle_important", "Toggle Important"),
        Binding("h", "collapse_node", "Collapse", show=False),
        Binding("l", "expand_node", "Expand", show=False),
        Binding("left", "collapse_node", "Collapse", show=False),
        Binding("right", "expand_node", "Expand", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def on_mount(self) -> None:
        if not getattr(self.app, "ast_mode", False):
            if hasattr(self, "unbind"):
                self.unbind("i")
            elif hasattr(self._bindings, "keys"):
                self._bindings.keys.pop("i", None)

    def action_toggle_select(self) -> None:
        self.app.action_toggle_node()

    def action_expand_node(self) -> None:
        node = self.cursor_node
        if node:
            node.expand()

    def action_collapse_node(self) -> None:
        node = self.cursor_node
        if node:
            if node.is_expanded:
                node.collapse()
            elif node.parent:
                self.select_node(node.parent)

    def action_toggle_important(self) -> None:
        if getattr(self.app, "ast_mode", False):
            self.app.action_toggle_important()

from cc_utils import extract_blocks, safe_read_file

class FileSelector(App):
    """Full-screen TUI for selecting which files to include."""
    CSS = """
    Screen {
        background: #2d2825;
    }
    SelectionTree {
        background: #2d2825;
        color: #ead6c9;
        padding: 1 2;
        scrollbar-color: #5a4d45;
        scrollbar-color-hover: #d08c60;
        scrollbar-color-active: #d08c60;
    }
    SelectionTree:focus > .tree--cursor {
        background: #d08c60;
        color: #2d2825;
        text-style: bold;
    }
    SelectionTree > .tree--guides {
        color: #5a4d45;
    }
    SelectionTree > .tree--guides-hover {
        color: #d08c60;
    }
    #path-display {
        background: #3c3431;
        color: #ead6c9;
        padding: 0 1;
        height: auto;
        border-top: solid #5a4d45;
        overflow: hidden;
    }
    Header {
        background: #d08c60;
        color: #2d2825;
    }
    Footer {
        background: #3c3431;
    }
    Footer > .footer--key {
        background: #d08c60;
        color: #2d2825;
    }
    Footer > .footer--description {
        color: #ead6c9;
    }
    """
    BINDINGS = [
        Binding("a", "select_all", "Select All"),
        Binding("n", "select_none", "Deselect All"),
        Binding("s", "focus_search", "Search"),
        Binding("i", "toggle_important", "Toggle Important"),
        Binding("q", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
    ]
    TITLE = "CombineCopy — File Selector"

    def __init__(self, root_dir: str, files: list[str], ast_mode: bool = False):
        super().__init__()
        self.root_dir = root_dir
        self.all_files = files
        self.search_term = ""
        self.ast_mode = ast_mode

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search files (Enter to jump to tree)...", id="search-input")
        yield SelectionTree("root", id="file-tree")
        yield Label("", id="path-display")
        yield Footer()

    def on_mount(self) -> None:
        self._build_tree()
        self._update_subtitle()
        self.query_one("#file-tree").focus()
        if not self.ast_mode:
            if hasattr(self, "unbind"):
                self.unbind("i")
            elif hasattr(self._bindings, "keys"):
                self._bindings.keys.pop("i", None)

    def on_input_changed(self, event: Input.Changed) -> None:
        self.search_term = event.value



    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._build_tree()
        self.query_one("#file-tree").focus()

    @staticmethod
    def _make_label(name: str, state: str, node_type: str, important: bool = False, ast_mode: bool = False) -> Text:
        if node_type == "folder":
            icon = "📂 "
        elif node_type == "block":
            icon = "ƒ "
        else:
            icon = "📄 "
            
        label = Text()
        if ast_mode and important and node_type != "block":
            label.append("[FULL] ", style="bold magenta")
        elif state == "checked":
            label.append("☑ ", style="bold green")
        elif state == "partial":
            label.append("▣ ", style="bold yellow")
        else:
            label.append("☐ ", style="bold red")
            
        label.append(icon + name, style="bold" if node_type == "folder" else "")
        return label

    def _build_tree(self) -> None:
        tree = self.query_one("#file-tree", SelectionTree)
        tree.clear()
        root_name = os.path.basename(self.root_dir) or self.root_dir
        tree.root.data = {"type": "folder", "state": "checked", "name": root_name}

        nodes_cache = {"": tree.root}

        for file_path in self.all_files:
            rel_path = os.path.relpath(file_path, self.root_dir)
            if self.search_term and self.search_term.lower() not in rel_path.lower():
                continue

            parts = rel_path.replace("\\", "/").split("/")
            current_node = tree.root
            path_so_far = ""
            for i, part in enumerate(parts):
                # If searching, we want new files to be 'unchecked' by default so they aren't auto-selected
                is_searching = bool(self.search_term)
                default_file_state = "unchecked" if is_searching else "checked"
                default_folder_state = "checked" # Folders stay checked to keep hierarchy visible

                if i == len(parts) - 1:
                    file_node = current_node.add(
                        self._make_label(part, default_file_state, "file", False, self.ast_mode),
                        data={"type": "file", "state": default_file_state, "important": False, "name": part, "path": file_path}
                    )
                    
                    content = safe_read_file(file_path)
                    if content and not content.startswith("(This is a binary"):
                        blocks = extract_blocks(file_path, content)
                        for idx, b in enumerate(blocks):
                            file_node.add_leaf(
                                self._make_label(b["name"], default_file_state, "block", False, self.ast_mode),
                                data={"type": "block", "state": default_file_state, "file_path": file_path, "block_idx": idx, "name": b["name"], "start": b["start"], "end": b["end"]}
                            )
                else:
                    path_so_far = f"{path_so_far}/{part}" if path_so_far else part
                    if path_so_far in nodes_cache:
                        current_node = nodes_cache[path_so_far]
                    else:
                        new_node = current_node.add(
                            self._make_label(part, default_folder_state, "folder", False, self.ast_mode),
                            data={"type": "folder", "state": default_folder_state, "important": False, "name": part},
                        )
                        nodes_cache[path_so_far] = new_node
                        current_node = new_node
        self._update_folder_states(tree.root)
        tree.root.expand()

    def _update_folder_states(self, node) -> str:
        if not node.children:
            return node.data.get("state", "unchecked") if node.data else "unchecked"

        has_checked = False
        has_unchecked = False
        has_partial = False

        for child in node.children:
            c_state = self._update_folder_states(child)
            if c_state == "checked":
                has_checked = True
            elif c_state == "unchecked":
                has_unchecked = True
            elif c_state == "partial":
                has_partial = True

        if has_partial or (has_checked and has_unchecked):
            final_state = "partial"
        elif has_checked:
            final_state = "checked"
        else:
            final_state = "unchecked"

        if node.data:
            node.data["state"] = final_state
            node.set_label(self._make_label(node.data["name"], final_state, node.data["type"], node.data.get("important", False), self.ast_mode))
        return final_state

    def _set_state(self, node, state: str) -> None:
        if node.data is None:
            return
        node.data["state"] = state
        node.set_label(self._make_label(node.data["name"], state, node.data["type"], node.data.get("important", False), self.ast_mode))
        for child in node.children:
            self._set_state(child, state)

    def _set_important(self, node, important: bool) -> None:
        if node.data is None:
            return
        node.data["important"] = important
        node.set_label(self._make_label(node.data["name"], node.data.get("state", "unchecked"), node.data["type"], important, self.ast_mode))
        for child in node.children:
            self._set_important(child, important)

    def _count_selected(self, node=None) -> int:
        if node is None:
            node = self.query_one("#file-tree", SelectionTree).root
        count = 0
        if node.data and node.data["type"] == "file":
            if node.data.get("state") in ("checked", "partial"):
                return 1
            return 0
        for child in node.children:
            count += self._count_selected(child)
        return count

    def _update_subtitle(self) -> None:
        self.sub_title = f"{self._count_selected()}/{len(self.all_files)} files selected"

    def _update_parent_states(self, node) -> None:
        parent = node.parent
        while parent and parent.data:
            has_checked = False
            has_unchecked = False
            has_partial = False
            
            for child in parent.children:
                c_state = child.data.get("state", "unchecked") if child.data else "unchecked"
                if c_state == "checked": has_checked = True
                elif c_state == "unchecked": has_unchecked = True
                elif c_state == "partial": has_partial = True
                
            if has_partial or (has_checked and has_unchecked):
                final_state = "partial"
            elif has_checked:
                final_state = "checked"
            else:
                final_state = "unchecked"
                
            if parent.data.get("state") != final_state:
                parent.data["state"] = final_state
                parent.set_label(self._make_label(parent.data["name"], final_state, parent.data["type"], parent.data.get("important", False), self.ast_mode))
                parent = parent.parent
            else:
                break

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        node = event.node
        if node and node.data:
            path = node.data.get("path")
            if not path:
                parts = []
                curr = node
                while curr and curr.data and curr != self.query_one("#file-tree").root:
                    parts.append(curr.data["name"])
                    curr = curr.parent
                path_str = "/ ".join(reversed(parts))
                display_text = Text.assemble((" Folder: ", "bold cyan"), path_str)
            else:
                rel_path = os.path.relpath(path, self.root_dir)
                dirname = os.path.dirname(rel_path)
                filename = os.path.basename(rel_path)
                display_text = Text()
                display_text.append(" File: ", style="bold cyan")
                if dirname:
                    display_text.append(f"{dirname}/", style="dim")
                display_text.append(filename, style="bold yellow")
            self.query_one("#path-display", Label).update(display_text)

    def action_toggle_node(self) -> None:
        tree = self.query_one("#file-tree", SelectionTree)
        node = tree.cursor_node
        if not node or not node.data:
            return
        new_state = "unchecked" if node.data.get("state") == "checked" else "checked"
        self._set_state(node, new_state)
        self._update_parent_states(node)
        self._update_subtitle()

    def action_toggle_important(self) -> None:
        if not self.ast_mode:
            return
        tree = self.query_one("#file-tree", SelectionTree)
        node = tree.cursor_node
        if not node or not node.data:
            return
        if node.data["type"] == "folder":
            new_state = not node.data.get("important", False)
            self._set_important(node, new_state)
            self._update_parent_states(node)
        else:
            new_state = not node.data.get("important", False)
            if new_state and node.data.get("state") == "unchecked":
                self._set_state(node, "checked")
                self._update_parent_states(node)
            self._set_important(node, new_state)
        self._update_subtitle()

    def action_focus_search(self) -> None:
        self.query_one("#search-input").focus()

    def on_tree_node_selected(self, event) -> None:
        self.action_toggle_node()

    def action_select_all(self) -> None:
        self._set_state(self.query_one("#file-tree", SelectionTree).root, "checked")
        self._update_subtitle()

    def action_select_none(self) -> None:
        self._set_state(self.query_one("#file-tree", SelectionTree).root, "unchecked")
        self._update_subtitle()

    def action_confirm(self) -> None:
        selected_files = []
        important_files = []
        partial_files = {}
        
        def walk(node):
            if node.data and node.data["type"] == "file":
                p = node.data["path"]
                if node.data["state"] == "checked":
                    selected_files.append(p)
                    if not self.ast_mode:
                        important_files.append(p)
                elif node.data["state"] == "partial":
                    blocks = []
                    for child in node.children:
                        if child.data and child.data["state"] == "checked":
                            blocks.append({
                                "name": child.data["name"],
                                "start": child.data["start"],
                                "end": child.data["end"]
                            })
                    if blocks:
                        partial_files[p] = blocks
                        selected_files.append(p)
                if self.ast_mode and node.data.get("important"):
                    important_files.append(p)
            else:
                for child in node.children:
                    walk(child)
                    
        walk(self.query_one("#file-tree", SelectionTree).root)
        self.exit((selected_files, important_files, partial_files))

    def action_cancel(self) -> None:
        self.exit(None)

def run_file_selector(root_dir: str, files: list[str], ast_mode: bool = False):
    """Launch the file-selector TUI. Returns selected paths, or None if cancelled."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_in, \
             tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_out:
            in_name = f_in.name
            out_name = f_out.name
            
        try:
            with open(in_name, "w", encoding="utf-8") as f:
                json.dump(files, f)
                
            script_path = os.path.abspath(__file__)
            subprocess.run([sys.executable, script_path, root_dir, in_name, out_name, str(ast_mode)], check=True)
            
            if os.path.exists(out_name):
                with open(out_name, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content.strip():
                        return json.loads(content)
            return None
        finally:
            for p in (in_name, out_name):
                try:
                    os.remove(p)
                except Exception:
                    pass
    else:
        app = FileSelector(root_dir, files, ast_mode=ast_mode)
        return app.run()

if __name__ == "__main__":
    if len(sys.argv) >= 5:
        r_dir = sys.argv[1]
        in_path = sys.argv[2]
        out_path = sys.argv[3]
        a_mode = sys.argv[4].lower() == "true"
        
        with open(in_path, "r", encoding="utf-8") as f_in:
            f_list = json.load(f_in)
            
        app = FileSelector(r_dir, f_list, ast_mode=a_mode)
        res = app.run()
        
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(res, f_out)
