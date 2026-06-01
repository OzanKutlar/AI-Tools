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
        self.selected_paths = set(files)
        self.important_paths = set()
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
        if hasattr(self, "_search_timer"):
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.25, self._debounced_search)

    def _debounced_search(self) -> None:
        self._build_tree()
        self._update_subtitle()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#file-tree").focus()

    @staticmethod
    def _make_label(name: str, selected: bool, node_type: str, important: bool = False, ast_mode: bool = False) -> Text:
        icon = "📂 " if node_type == "folder" else "📄 "
        label = Text()
        if ast_mode and important:
            label.append("[FULL] ", style="bold magenta")
        elif selected:
            label.append("☑ ", style="bold green")
        else:
            label.append("☐ ", style="bold red")
        if node_type == "folder":
            label.append(icon + name, style="bold")
        else:
            label.append(icon + name)
        return label

    def _build_tree(self) -> None:
        tree = self.query_one("#file-tree", SelectionTree)
        tree.clear()
        root_name = os.path.basename(self.root_dir) or self.root_dir
        tree.root.data = {"type": "folder", "selected": True, "name": root_name}

        nodes_cache = {"": tree.root}

        for file_path in self.all_files:
            rel_path = os.path.relpath(file_path, self.root_dir)
            if self.search_term and self.search_term.lower() not in rel_path.lower():
                continue

            parts = rel_path.replace("\\", "/").split("/")
            current_node = tree.root
            path_so_far = ""
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    is_selected = file_path in self.selected_paths
                    is_important = file_path in self.important_paths
                    current_node.add_leaf( 
                        self._make_label(part, is_selected, "file", is_important, self.ast_mode),
                        data={"type": "file", "selected": is_selected, "important": is_important, "name": part, "path": file_path},
                    )
                else:
                    path_so_far = f"{path_so_far}/{part}" if path_so_far else part
                    if path_so_far in nodes_cache:
                        current_node = nodes_cache[path_so_far]
                    else:
                        new_node = current_node.add(
                            self._make_label(part, True, "folder", False, self.ast_mode),
                            data={"type": "folder", "selected": True, "important": False, "name": part},
                        )
                        nodes_cache[path_so_far] = new_node
                        current_node = new_node
        self._update_folder_states(tree.root)
        tree.root.expand()

    def _update_folder_states(self, node) -> bool:
        if not node.children and node.data and node.data.get("type") == "folder":
            return node.data.get("selected", False)
        if node.data and node.data.get("type") == "file":
            return node.data.get("selected", False)
            
        all_selected = True
        has_children = False
        for child in node.children:
            has_children = True
            if not self._update_folder_states(child):
                all_selected = False
                
        if not has_children:
            all_selected = node.data.get("selected", False) if node.data else False
                
        if node.data:
            node.data["selected"] = all_selected
            node.set_label(self._make_label(node.data["name"], all_selected, node.data["type"], node.data.get("important", False), self.ast_mode))
        return all_selected

    def _set_selected(self, node, selected: bool) -> None:
        if node.data is None:
            return
        node.data["selected"] = selected
        node.set_label(self._make_label(node.data["name"], selected, node.data["type"], node.data.get("important", False), self.ast_mode))
        if node.data.get("type") == "file":
            p = node.data.get("path")
            if p:
                if selected:
                    self.selected_paths.add(p)
                else:
                    self.selected_paths.discard(p)
        for child in node.children:
            self._set_selected(child, selected)

    def _set_important(self, node, important: bool) -> None:
        if node.data is None:
            return
        node.data["important"] = important
        node.set_label(self._make_label(node.data["name"], node.data.get("selected", False), node.data["type"], important, self.ast_mode))
        if node.data.get("type") == "file":
            p = node.data.get("path")
            if p:
                if important:
                    self.important_paths.add(p)
                else:
                    self.important_paths.discard(p)
        for child in node.children:
            self._set_important(child, important)

    def _count_selected(self, node=None) -> int:
        if node is None:
            node = self.query_one("#file-tree", SelectionTree).root
        count = 0
        if node.data and node.data["type"] == "file" and node.data["selected"]:
            count = 1
        for child in node.children:
            count += self._count_selected(child)
        return count

    def _update_subtitle(self) -> None:
        self.sub_title = f"{self._count_selected()}/{len(self.all_files)} files selected"

    def _update_parent_states(self, node) -> None:
        parent = node.parent
        while parent and parent.data:
            all_selected = True
            if not parent.children:
                all_selected = False
            else:
                for child in parent.children:
                    if child.data and not child.data.get("selected", False):
                        all_selected = False
                        break
            if parent.data.get("selected") != all_selected:
                parent.data["selected"] = all_selected
                parent.set_label(self._make_label(parent.data["name"], all_selected, parent.data["type"], parent.data.get("important", False), self.ast_mode))
                parent = parent.parent
            else:
                break

    def _collect_selected(self, node) -> tuple[list[str], list[str]]:
        selected: list[str] = []
        important: list[str] = []
        if node.data and node.data["type"] == "file":
            if node.data.get("selected"):
                selected.append(node.data["path"])
                if not self.ast_mode:
                    important.append(node.data["path"])
            if self.ast_mode and node.data.get("important"):
                important.append(node.data["path"])
        for child in node.children:
            c_sel, c_imp = self._collect_selected(child)
            selected.extend(c_sel)
            important.extend(c_imp)
        return selected, important

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
        new_state = not node.data["selected"]
        self._set_selected(node, new_state)
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
            if new_state and not node.data.get("selected", False):
                self._set_selected(node, True)
                self._update_parent_states(node)
            self._set_important(node, new_state)
        self._update_subtitle()

    def action_focus_search(self) -> None:
        self.query_one("#search-input").focus()

    def on_tree_node_selected(self, event) -> None:
        self.action_toggle_node()

    def action_select_all(self) -> None:
        self._set_selected(self.query_one("#file-tree", SelectionTree).root, True)
        self._update_subtitle()

    def action_select_none(self) -> None:
        self._set_selected(self.query_one("#file-tree", SelectionTree).root, False)
        self._update_subtitle()

    def action_confirm(self) -> None:
        selected = [p for p in self.all_files if p in self.selected_paths]
        if self.ast_mode:
            important = [p for p in self.all_files if p in self.important_paths]
        else:
            important = list(selected)
        self.exit((selected, important))

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
