import os
import argparse
import math
import time
import json
import difflib
import re
import subprocess
import threading
import sys
import tempfile
import zipfile
import atexit
import shutil
from rich.console import Console

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.rule import Rule
from rich import box
from rich.text import Text
from rich.style import Style
import pyperclip

# Textual imports
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Tree, Header, Footer, Markdown, ListView, ListItem, Label, Button, Static, RichLog, TextArea, Input
from textual.binding import Binding
from textual.screen import ModalScreen

# Initialize Rich Console
console = Console()

FILE_CULLING_PROMPT = r"""
FILE CULLING (Repo Map Phase): If the user provides a "DIRECTORY TREE", analyze the request and the tree. **CRITICAL: You must output a final list of all file paths that are relevant to the task or will be modified. Output this strictly as a simple JSON array of strings.** This helps the user ensure only necessary context is loaded for the execution. Do not output anything else. Example:["src/main.py", "tests/test_main.py"]
"""

ORCHESTRATE_SYSTEM_PROMPT_TEMPLATE = r"""<identity>
You are Antigravity Orchestrator, a powerful agentic AI coding assistant designed by the Google Deepmind team.
You are pair programming with a USER to solve their coding task. Rather than writing code directly, you operate as a highly capable architect and planner. Your job is to analyze the user's request, formulate a precise plan, and output an orchestration payload containing exact specifications and the required files for a less capable downstream model to execute.
</identity>

<mode_descriptions>
You operate across two core phases of work. Clearly communicate to the user which phase you are currently in:

PLANNING: Analyze the provided code, understand requirements, and design your approach. You must always start in PLANNING mode and present your plan to document your proposed changes and get user approval. The planning mode should never be written in JSON format.
{FILE_CULLING_INSTRUCTION}
ORCHESTRATE: Once the user approves your plan, output the files needed and precise specifications. **CRITICAL: You must output your entire response strictly in pure JSON format, wrapped in a markdown code block (i.e., use ```json and ```).** The script relies on this exact schema:

{
  "phase": "ORCHESTRATE",
  "markdown": "Your explanations, thoughts, and conversational text formatted in standard markdown.",
  "files": [
    "relative/path/to/relevant_file1.py",
    "relative/path/to/relevant_file2.py"
  ],
  "original_request": "The exact original request provided by the user.",
  "prompt": "Highly detailed instructions for the execution model. List EXACTLY what libraries, functions, and variables to modify. Provide pseudo-code or specific search/replace requirements to ensure the downstream model cannot fail."
}

**Orchestration Constraints:**
1. **CRITICAL JSON FORMATTING**: You MUST properly escape all internal double quotes (`\"`) and backslashes (`\\`) inside your string values. Failing to escape quotes will break the JSON parser.
</mode_descriptions>
"""

DEFAULT_SYSTEM_PROMPT_TEMPLATE = r"""<identity>
You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.
You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.
The USER will send you requests, which you must always prioritize addressing. The USER will provide all necessary file contents, context, and environment state directly in the prompt.
</identity>

<mode_descriptions>
You operate across three core phases of work. Clearly communicate to the user which phase you are currently in:

PLANNING: Analyze the provided code, understand requirements, and design your approach. You must always start in PLANNING mode and present an `implementation_plan.md` to document your proposed changes and get user approval, unless the user explicitly asks you not to plan in their message. If the user requests changes to your plan, stay in PLANNING mode, update the plan, and request review again until approved. CRITICAL: The planning mode should never be written in JSON format or wrapped in code blocks. It should always be written in raw markdown.
{FILE_CULLING_INSTRUCTION}
EXECUTION: Write code, make changes, and implement your design. **CRITICAL: You must output your entire response strictly in pure JSON format, wrapped in a markdown code block (i.e., use ```json and ```).** The downstream automated agent relies on this exact schema:

{
  "phase": "EXECUTION",
  "markdown": "Your explanations, thoughts, and conversational text formatted in standard markdown.",
  "commit_message": "Conventional git commit message detailing the changes.",
  "files": [
    // Rule 1: CREATE - For brand new files.
    {
      "action": "create",
      "path": "relative/path/to/new_file.py",
      "content": "The COMPLETE, fully functional source code of the new file."
    },
    // Rule 2: MODIFY - For making partial updates to existing files.
    // ALWAYS use `search_replace` blocks for modifications. It is highly efficient and preferred.
    {
      "action": "modify",
      "path": "relative/path/to/existing_file.py",
      "search_replace": [
        {
          "search": "The EXACT lines of existing code to replace. You MUST include sufficient context lines. Your search string MUST perfectly match the original file's whitespace and indentation.",
          "replace": "The new code that will replace the searched block."
        }
      ]
    },
    // Rule 2 Alternate: If a file is extremely small, or you are completely overwriting it, use "content" instead.
    {
      "action": "modify",
      "path": "relative/path/to/tiny_file.py",
      "content": "The COMPLETE, fully updated source code."
    },
    // Rule 2 Alternate 2: REGEX MASS REPLACE - For replacing patterns across a file.
    {
      "action": "modify",
      "path": "relative/path/to/existing_file.py",
      "regex_replace": [
        {
          "pattern": "\\bWizard\\b",
          "replacement": "Witch"
        }
      ]
    },
    // Rule 3: DELETE - For removing files.
    {
      "action": "delete",
      "path": "relative/path/to/dead_file.py",
      "content": "" 
    }
  ]
}

**Execution Constraints:** 1. You must explicitly define boundaries for the downstream agent. 
2. Never use CLI tools. Restrict your commands purely to file creation, modification, or deletion via the JSON payload above.
3. **CRITICAL JSON FORMATTING**: You MUST properly escape all internal double quotes (`\"`) and backslashes (`\\`) inside your string values (e.g., HTML attributes like `class=\"flex\"` or regex patterns). Failing to escape quotes will break the JSON parser.
4. **Error Recovery**: If the user provides an error regarding a specific file modification (e.g., a search/replace mismatch or JSON syntax error), your next EXECUTION payload must contain ONLY the file that needs correction. Do not re-include other files from the previous payload.

VERIFICATION: Test your changes conceptually and validate correctness. Ask the user to run specific commands or tests to verify the code, and evaluate the outputs they provide. Create a `walkthrough.md` after completing verification to document what was accomplished, what was tested, and validation results.
</mode_descriptions>

<task_artifact>
Path: C:\Users\Ozan\task.md 
<description> 
**Purpose**: A detailed checklist to organize your work. Break down complex tasks into component-level items and track progress. Start with an initial breakdown and maintain it as a living document.
**Format**: 
- `[ ]` uncompleted tasks 
- `[/]` in progress tasks 
- `[x]` completed tasks 
- Use indented lists for sub-items 
**Updating task.md**: Present the updated task list to the user as you progress through your checklist during planning, execution, and verification.
</description>
</task_artifact>

<implementation_plan_artifact>
Path: C:\Users\Ozan\implementation_plan.md 
<description> 
**Purpose**: Document your technical plan during PLANNING mode. Present it to the user for review, update based on feedback, and repeat until the user approves before proceeding to EXECUTION.
**Format**: Use the following format for the implementation plan. Omit any irrelevant sections.

# [Goal Description]
Provide a brief description of the problem, any background context, and what the change accomplishes.

## User Review Required
Document anything that requires user review or clarification, for example, breaking changes or significant design decisions. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items. If there are no such items, omit this section entirely.

## Proposed Changes
Group files by component (e.g., package, feature area, dependency layer) and order logically (dependencies first). Separate components with horizontal rules for visual clarity.

### [Component Name]
Summary of what will change in this component, separated by files. For specific files, Use [NEW] and [DELETE] to demarcate new and deleted files.

## Verification Plan
Summary of how the changes will be verified.
### Automated Tests 
- Exact commands the user should run, browser testing instructions, etc.
### Manual Verification 
- Asking the user to deploy to staging, verify UI changes, etc.
</description>
</implementation_plan_artifact>

<walkthrough_artifact>
Path: walkthrough.md 
**Purpose**: After completing work, summarize what you accomplished. Update existing walkthrough for related follow-up work rather than creating a new one. 
**Document**: 
- Changes made 
- What was tested 
- Validation results (based on user feedback)
</walkthrough_artifact>

<artifact_formatting_guidelines>
Here are some formatting tips for artifacts that you choose to write as markdown files with the .md extension:

# Markdown Formatting
When creating markdown artifacts, use standard markdown and GitHub Flavored Markdown formatting.

## Alerts
Use GitHub-style alerts strategically to emphasize critical information:
  > [!NOTE] Background context or helpful explanations
  > [!TIP] Performance optimizations or best practices
  > [!IMPORTANT] Essential requirements
  > [!WARNING] Breaking changes or potential problems
  > [!CAUTION] High-risk actions

## Code and Diffs
Use fenced code blocks with language specification for syntax highlighting.
Use diff blocks to show code changes. Prefix lines with + for additions, - for deletions, and a space for unchanged lines:
```diff
-old_function_name()
+new_function_name()
 unchanged_line()
```

## Commit Messages
When generating commit messages, you MUST strictly adhere to this exact template format (including spacing, colons, and newlines):
type(scope) : description
extra desc
 extra desc

Example:
feat(xyz) : description
extra desc
 extra desc

## Mermaid Diagrams

Create mermaid diagrams using fenced code blocks with language `mermaid` to visualize complex relationships, workflows, and architectures.

## Tables

Use standard markdown table syntax to organize structured data.

## File Links

- Create clickable file links using standard markdown link syntax for readability, but do not rely on them for actual navigation since the user is managing files manually.
    
    </artifact_formatting_guidelines>
    
<user_rules>

The user has not defined any custom rules.

</user_rules>

<coding_standards>
You must adhere to the following high-reliability coding standards, inspired by mission-critical environments:
1. **Small Functions:** Keep functions short and focused on a single responsibility.
2. **Defensive Inputs:** Validate all incoming parameters and handle impossible states early.
3. **Bounded Loops:** Avoid unbounded `while` loops. Ensure all iterations have fixed, logical upper bounds to prevent hanging.
4. **Explicit Error Handling:** Do not silently swallow errors. All asynchronous or external I/O operations must be wrapped in explicit error-handling blocks.
5. **Minimal Scope:** Declare variables at the smallest possible scope. Avoid global state whenever possible and favor immutable assignments.
</coding_standards>

<communication_style>

- **Formatting**. Format your responses in github-style markdown to make your responses easier for the USER to parse. Use headers, bold text, and backticks.
    
- **Proactiveness**. You are allowed to be proactive, but only in the course of completing the user's task. Anticipate next steps and provide the necessary code or instructions, but avoid surprising the user or jumping to conclusions before fully understanding their goal.
    
- **Helpfulness**. Respond like a helpful software engineer who is explaining your work to a friendly collaborator on the project. Acknowledge mistakes or any backtracking you do.
    
- **Ask for clarification**. If you are unsure about the USER's intent or need to see the contents of a specific file to proceed safely, always ask the user to provide that information rather than making assumptions.
    
    </communication_style>"""


class SelectionTree(Tree):
    """Custom Tree that prevents Enter from expanding nodes and maps vim keys."""

    BINDINGS = [
        Binding("enter", "toggle_select", "Toggle Selection"),
        Binding("space", "toggle_select", "Toggle Selection"),
        Binding("h", "collapse_node", "Collapse", show=False),
        Binding("l", "expand_node", "Expand", show=False),
        Binding("left", "collapse_node", "Collapse", show=False),
        Binding("right", "expand_node", "Expand", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

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
        Binding("q", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
    ]

    TITLE = "CombineCopy \u2014 File Selector"

    def __init__(self, root_dir: str, files: list[str]):
        super().__init__()
        self.root_dir = root_dir
        self.all_files = files
        self.selected_paths = set(files)
        self.search_term = ""

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

    def on_input_changed(self, event: Input.Changed) -> None:
        self.search_term = event.value
        self._build_tree()
        self._update_subtitle()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#file-tree").focus()

    # ── Label helpers ───────────────────────────────────────

    @staticmethod
    def _make_label(name: str, selected: bool, node_type: str) -> Text:
        icon = "\U0001f4c1 " if node_type == "folder" else "\U0001f4c4 "
        label = Text()
        if selected:
            label.append("\u2611 ", style="bold green")
        else:
            label.append("\u2610 ", style="bold red")
        if node_type == "folder":
            label.append(icon + name, style="bold")
        else:
            label.append(icon + name)
        return label

    # ── Tree construction ───────────────────────────────────────

    def _build_tree(self) -> None:
        tree = self.query_one("#file-tree", SelectionTree)
        tree.clear()
        root_name = os.path.basename(self.root_dir) or self.root_dir
        tree.root.data = {"type": "folder", "selected": True, "name": root_name}

        for file_path in self.all_files:
            rel_path = os.path.relpath(file_path, self.root_dir)
            if self.search_term and self.search_term.lower() not in rel_path.lower():
                continue

            parts = rel_path.replace("\\", "/").split("/")
            current_node = tree.root
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    # Leaf file node
                    is_selected = file_path in self.selected_paths
                    current_node.add_leaf(
                        self._make_label(part, is_selected, "file"),
                        data={"type": "file", "selected": is_selected,
                              "name": part, "path": file_path},
                    )
                else:
                    # Find or create intermediate folder node
                    found = None
                    for child in current_node.children:
                        if (child.data
                                and child.data.get("type") == "folder"
                                and child.data.get("name") == part):
                            found = child
                            break
                    if found:
                        current_node = found
                    else:
                        new_node = current_node.add(
                            self._make_label(part, True, "folder"),
                            data={"type": "folder", "selected": True, "name": part},
                        )
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
            node.set_label(self._make_label(node.data["name"], all_selected, node.data["type"]))
        return all_selected

    # ── Selection logic ─────────────────────────────────────────

    def _set_selected(self, node, selected: bool) -> None:
        """Set *selected* state on *node* and all its descendants."""
        if node.data is None:
            return
        node.data["selected"] = selected
        node.set_label(
            self._make_label(node.data["name"], selected, node.data["type"])
        )
        for child in node.children:
            self._set_selected(child, selected)

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
        """Recursively update parent folder selection icons based on children."""
        parent = node.parent
        while parent and parent.data:
            # A folder is 'selected' (checked) only if all its visible children are selected
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
                parent.set_label(self._make_label(parent.data["name"], all_selected, parent.data["type"]))
                parent = parent.parent
            else:
                break

    def _collect_selected(self, node) -> list[str]:
        result: list[str] = []
        if node.data and node.data["type"] == "file" and node.data["selected"]:
            result.append(node.data["path"])
        for child in node.children:
            result.extend(self._collect_selected(child))
        return result

    # ── Event handlers / actions ────────────────────────────────

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Update path display when navigation occurs."""
        node = event.node
        if node and node.data:
            path = node.data.get("path")
            if not path:
                # It's a folder, construct relative path
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
        """Toggle selection for the currently focused node."""
        tree = self.query_one("#file-tree", SelectionTree)
        node = tree.cursor_node
        if not node or not node.data:
            return
        
        new_state = not node.data["selected"]
        self._set_selected(node, new_state)
        self._update_parent_states(node)
        self._update_subtitle()

    def action_focus_search(self) -> None:
        self.query_one("#search-input").focus()

    def on_tree_node_selected(self, event) -> None:
        """Handle mouse clicks or Enter (if not overridden by action_toggle_node)."""
        self.action_toggle_node()

    def action_select_all(self) -> None:
        self._set_selected(self.query_one("#file-tree", SelectionTree).root, True)
        self._update_subtitle()

    def action_select_none(self) -> None:
        self._set_selected(self.query_one("#file-tree", SelectionTree).root, False)
        self._update_subtitle()

    def action_confirm(self) -> None:
        tree = self.query_one("#file-tree", SelectionTree)
        self.exit(self._collect_selected(tree.root))

    def action_cancel(self) -> None:
        self.exit(None)


def run_file_selector(root_dir: str, files: list[str]):
    """Launch the file-selector TUI. Returns selected paths, or None if cancelled."""
    app = FileSelector(root_dir, files)
    return app.run()


def render_word_diff(old_text: str, new_text: str, diff_view: RichLog) -> None:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            if i2 - i1 > 6:
                if i1 != 0:
                    for line in old_lines[i1:i1+3]:
                        diff_view.write(Text("  " + line.rstrip('\n'), style="dim"))
                diff_view.write(Text("...", style="bold dim"))
                if i2 != len(old_lines):
                    for line in old_lines[i2-3:i2]:
                        diff_view.write(Text("  " + line.rstrip('\n'), style="dim"))
            else:
                for line in old_lines[i1:i2]:
                    diff_view.write(Text("  " + line.rstrip('\n'), style="dim"))
        else:
            old_hunk = "".join(old_lines[i1:i2])
            new_hunk = "".join(new_lines[j1:j2])
            
            old_words = re.findall(r'\S+|\s+', old_hunk)
            new_words = re.findall(r'\S+|\s+', new_hunk)
            
            word_matcher = difflib.SequenceMatcher(None, old_words, new_words)
            
            diff_text = Text()
            for w_tag, wi1, wi2, wj1, wj2 in word_matcher.get_opcodes():
                if w_tag == 'equal':
                    diff_text.append("".join(old_words[wi1:wi2]))
                elif w_tag == 'delete':
                    diff_text.append("".join(old_words[wi1:wi2]), style="white on red")
                elif w_tag == 'insert':
                    diff_text.append("".join(new_words[wj1:wj2]), style="black on green")
                elif w_tag == 'replace':
                    diff_text.append("".join(old_words[wi1:wi2]), style="white on red strike")
                    diff_text.append("".join(new_words[wj1:wj2]), style="black on green")
            
            diff_view.write(diff_text)


def is_binary_file(filepath: str) -> bool:
    """Check if a file is binary by extension or by looking for null bytes in the first 8192 bytes."""
    binary_extensions = {
        '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff',
        '.zip', '.tar', '.gz', '.7z', '.rar',
        '.exe', '.dll', '.so', '.dylib', '.bin',
        '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.mp3', '.mp4', '.avi', '.mkv', '.mov', '.wav', '.flac',
        '.pyc', '.pyd', '.o', '.a', '.class', '.jar'
    }
    _, ext = os.path.splitext(filepath)
    if ext.lower() in binary_extensions:
        return True
        
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(8192)
            if b'\0' in chunk:
                return True
            return False
    except Exception:
        return False


def safe_read_file(path: str) -> str:
    """Tries to read a file as UTF-8, falls back to Windows-1252. Ignores binary files."""
    if is_binary_file(path):
        return "(This is a binary file)"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="cp1252") as f:
                return f.read()
        except UnicodeDecodeError:
            return "(This is a binary file)"


def intelligent_json_fix(content: str) -> tuple[dict | None, str]:
    """Iteratively attempts to heal common LLM JSON syntax errors (like unescaped quotes)."""
    
    # 1. Aggressive line-by-line quote fixer for known string fields
    lines = content.split('\n')
    for i, line in enumerate(lines):
        match = re.match(r'^(\s*"(?:markdown|commit_message|content|search|replace|pattern|replacement)"\s*:\s*")(.*)$', line)
        if match:
            prefix = match.group(1)
            rest = match.group(2)
            suffix_match = re.search(r'("\s*(?:,|},|})?\s*)$', rest)
            if suffix_match:
                suffix = suffix_match.group(1)
                value = rest[:-len(suffix)]
                # Escape all unescaped double quotes inside the value
                fixed_value = re.sub(r'(?<!\\)"', r'\\"', value)
                lines[i] = prefix + fixed_value + suffix
                
    current = '\n'.join(lines)

    # 2. Iterative fallback using the JSON parser's exact error positions
    for _ in range(200):
        try:
            data = json.loads(current)
            return data, current
        except json.JSONDecodeError as e:
            msg = e.msg
            pos = e.pos
        
            if "Invalid control character" in msg:
                if pos < len(current):
                    char = current[pos]
                    if char == '\n':
                        current = current[:pos] + '\\n' + current[pos+1:]
                    elif char == '\t':
                        current = current[:pos] + '\\t' + current[pos+1:]
                    elif char == '\r':
                        current = current[:pos] + '\\r' + current[pos+1:]
                    else:
                        current = current[:pos] + '\\u{:04x}'.format(ord(char)) + current[pos+1:]
                    continue
                    
            if "Invalid \\escape" in msg:
                # e.pos is usually the invalid character after the backslash
                if pos > 0 and current[pos-1] == '\\':
                    current = current[:pos-1] + '\\\\' + current[pos:]
                    continue
                
            if "Expecting ',' delimiter" in msg or "Expecting value" in msg or "Extra data" in msg:
            # Find the last unescaped quote before the error position
                last_quote = current.rfind('"', 0, pos)
                if last_quote != -1 and current[last_quote-1] != '\\':
                    current = current[:last_quote] + '\\"' + current[last_quote+1:]
                    continue
                    
            break
    return None, content


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
        # Select first candidate by default
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
        # start_line and end_line are 1-indexed
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


# ── AutoAgentApp (New TUI for --auto) ────────────────────────────

class JsonFileSelector(App):
    """TUI for selecting files based on a JSON list from the clipboard."""

    CSS = """
    Screen { background: #2d2825; }
    Header { background: #d08c60; color: #2d2825; }
    Footer { background: #3c3431; }
    #main-container { padding: 1 2; }
    .title { background: #4a3f39; color: #d08c60; padding: 1; text-style: bold; text-align: center; margin-bottom: 1; }
    ListView { border: solid #5a4d45; background: #241f1c; height: 1fr; }
    ListItem { height: auto; }
    """

    BINDINGS = [
        Binding("q", "confirm", "Confirm List"),
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "reload", "Reload Clipboard")
    ]

    def __init__(self, root_dir: str):
        super().__init__()
        self.root_dir = root_dir
        self.files = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-container"):
            yield Label("Waiting for JSON file list on clipboard...", id="status-title", classes="title")
            yield ListView(id="file-list")
            yield Static("\n[bold]Instructions:[/bold] Copy a JSON array of strings (e.g. [\"file1.py\", \"file2.py\"]) to proceed.")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.5, self.check_clipboard)

    def action_reload(self) -> None:
        self.check_clipboard(force=True)

    def check_clipboard(self, force=False) -> None:
        try:
            content = pyperclip.paste().strip()
            if not content:
                return
            
            # Basic heuristic: starts with [
            if content.startswith("["):
                data = json.loads(content)
                if isinstance(data, list) and data != self.files:
                    self.files = data
                    self.update_list(data)
        except Exception:
            pass

    def update_list(self, files: list[str]) -> None:
        list_view = self.query_one("#file-list", ListView)
        list_view.clear()
        self.query_one("#status-title", Label).update("[bold green]JSON File List Detected[/bold green]")
        for f in files:
            list_view.append(ListItem(Label(f)))

    def action_confirm(self) -> None:
        if self.files:
            # Convert relative to absolute based on root_dir
            abs_files = [os.path.abspath(os.path.join(self.root_dir, f)) for f in self.files]
            self.exit(abs_files)

    def action_cancel(self) -> None:
        self.exit(None)


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
                    # Heuristic: does it contain any of the search blocks?
                    blocks = step.get("blocks", [])
                    is_likely_file = False
                    if not blocks:
                        is_likely_file = True
                    else:
                        # Check first 5 blocks for presence
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
        
        # Run Search Replace
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
                # Fuzzy match attempt
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
        
        # Check if this was the last step for this specific file
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

    TITLE = "CombineCopy \u2014 Orchestrator Listener"

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
            
            if '"phase":' in content and '"ORCHESTRATE"' in content:
                json_blocks = self._extract_json_objects(content)
                for json_str in json_blocks:
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and data.get("phase") == "ORCHESTRATE" and "prompt" in data:
                            self.load_payload(data)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def load_payload(self, data: dict) -> None:
        self.polling_timer.pause()
        self.payload = data
        self.query_one("#status-label", Label).update("Orchestrator Payload Ready")
        self.query_one("#ai-markdown", Markdown).update(data.get("markdown", "No markdown provided."))
        
        # Combine original request and prompt for the preview
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
        
        # Combine into a single instruction block
        combined_prompt = []
        if original:
            combined_prompt.append(f"USER ORIGINAL REQUEST:\n{original}")
        if prompt:
            combined_prompt.append(f"ORCHESTRATOR ARCHITECTURE & INSTRUCTIONS:\n{prompt}")
        
        final_prompt_text = "\n\n".join(combined_prompt)
        
        buffer = []
        # Prompt Part 1: Start
        buffer.append("--- USER REQUEST ---")
        buffer.append(final_prompt_text)
        
        # System Instructions
        buffer.append("\n--- SYSTEM INSTRUCTIONS ---")
        sys_prompt = DEFAULT_SYSTEM_PROMPT_TEMPLATE.replace('{FILE_CULLING_INSTRUCTION}\n', '').replace('{FILE_CULLING_INSTRUCTION}', '')
        buffer.append(sys_prompt)
        
        # Prompt Part 2: Middle (Post-System)
        buffer.append("\n--- USER REQUEST ---")
        buffer.append(final_prompt_text)
        
        # File Context
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

        # Prompt Part 3: End (Reminder) 
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

    TITLE = "CombineCopy \u2014 Auto Agent Listener"

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
            self.title = "CombineCopy \u2014 Auto Agent Listener (REVERT MODE)"
        if self.web_mode:
            self.title = "CombineCopy \u2014 Auto Agent Listener (WEB MACRO MODE)"




    def action_reload(self) -> None:
        """Forces a re-read and re-parse of the clipboard."""
        self.last_clipboard = ""  # Reset this so the parser doesn't ignore the clipboard content
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
        # If requested, capture current clipboard so we only react to the next change
        if self.ignore_initial_clipboard:
            try:
                self.last_clipboard = pyperclip.paste().strip()
            except Exception:
                pass

        # Check clipboard every 500ms
        self.polling_timer = self.set_interval(0.5, self.check_clipboard)
        self.query_one("#diff-view", RichLog).write("Select a file to view diffs.")

    def _extract_json_objects(self, text: str) -> list[str]:
        """Extracts all well-formed top-level JSON objects from text using brace tracking."""
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
            
            # Heuristic to detect if text is an AI payload before parsing
            if '"phase":' in content and '"EXECUTION"' in content:
                # Isolate all potential JSON blocks using brace matching
                json_blocks = self._extract_json_objects(content)
                
                for json_str in json_blocks:
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and data.get("phase") == "EXECUTION" and "files" in data:
                            self.load_payload(data)
                            return  # Successfully loaded, stop checking other blocks
                    except json.JSONDecodeError as e:
                        # Intelligently fix common LLM JSON formatting mistakes
                        fixed_data, fixed_str = intelligent_json_fix(json_str)
                        if fixed_data and isinstance(fixed_data, dict) and fixed_data.get("phase") == "EXECUTION" and "files" in fixed_data:
                            self.notify("Intelligently auto-fixed JSON syntax errors!", title="Auto-Fix Success", severity="info")
                            self.load_payload(fixed_data)
                            return
                            
                        # If a block explicitly claims to be the EXECUTION payload but fails, show error
                        if '"EXECUTION"' in json_str and '"phase"' in json_str:
                            self.show_json_error(e, json_str)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _normalize_text(self, text: str) -> str:
        """Removes leading/trailing whitespace from each line and removes empty lines."""
        return "\n".join(
            line.strip()
            for line in text.strip().split('\n')
            if line.strip()
        )

    def show_json_error(self, error: json.JSONDecodeError, content: str) -> None:
        """Displays JSON syntax errors clearly so the user can relay them to the AI."""
        self.polling_timer.pause()
        self.broken_json_content = content
        self.query_one("#status-label", Label).update("[bold red]JSON Parse Error[/bold red]")
        
        # Extract the exact line that failed
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
        
        if len(search_lines) <= 1:
            return []
            
        search_norm = [line.strip() for line in search_lines]
        file_norm = [line.strip() for line in file_lines]
        
        matcher = difflib.SequenceMatcher(None, search_norm, file_norm)
        blocks = matcher.get_matching_blocks()
        
        best_n = 0
        candidates = []
        
        for block in blocks:
            if block.size > 0:
                matched_text = "".join(search_norm[block.a : block.a + block.size])
                if not matched_text:
                    continue
                    
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

        # --- Pre-validate modify blocks ---
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
                            # --- Fallback to Normalized Matching ---
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
                                # --- Fallback to Partial Matching ---
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
        """Populates the UI once a valid JSON payload is detected."""
        self.polling_timer.pause()  # Stop polling while user reviews
        
        # Normalize "replacement" to "replace" in search_replace blocks
        for file_obj in data.get("files", []):
            for block in file_obj.get("search_replace", []):
                if "replacement" in block and "replace" not in block:
                    block["replace"] = block.pop("replacement")

        # --- REVERT MODE MUTATIONS ---
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
        
        # Populate file list and pre-validate
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
        if not self.payload:
            return
        
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
        """Render the diff for a specific file index."""
        if not self.payload or idx < 0 or idx >= len(self.payload.get("files", [])):
            return
            
        self._update_buttons()
            
        file_obj = self.payload["files"][idx]
        path = file_obj.get("path")
        
        # Update Header with emphasized filename
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
                
        # Determine new content
        new_text = self._compute_new_text(file_obj, old_text)

        diff_view = self.query_one("#diff-view", RichLog)
        diff_view.clear()
        
        # Display validation errors and warnings prominently
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
            # Use different styling based on whether it's a hard error or just a warning
            style = "bold red" if errors else "bold yellow"
            diff_view.write(Text(header_text, style=style))

        if old_text == new_text:
            diff_view.write(Text("No changes detected.", style="dim"))
            return
            
        render_word_diff(old_text, new_text, diff_view)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Show diff when a file is highlighted via keyboard."""
        if event.item is not None and event.item.id and event.item.id.startswith("file-"):
            idx = int(event.item.id.split("-")[1])
            self._render_diff_for_index(idx)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Show diff when a file is clicked with the mouse."""
        if event.item is not None and event.item.id and event.item.id.startswith("file-"):
            idx = int(event.item.id.split("-")[1])
            self._render_diff_for_index(idx)

    # ── Shortcut Actions ──────────────────────────────
    
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
        if resolved_text is None:
            return
            
        file_obj = self.payload["files"][file_idx]
        file_obj["content"] = resolved_text  # Lock in the user's choices
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
        if selected_text is None:
            return
            
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
            # If changed, update clipboard and reload
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
            # Re-enable the button if still on the same error payload
            def re_enable():
                if self.query("Button#btn-fix-json"):
                    btn = self.query_one("#btn-fix-json", Button)
                    if hasattr(self, 'broken_json_content') and self.broken_json_content == current_text:
                        btn.disabled = False
            self.app.call_from_thread(re_enable)

    def action_copy_error(self) -> None:
        """Copies the current error (JSON or File Validation) to clipboard."""
        if self.json_error_text:
            pyperclip.copy(self.json_error_text)
            self.notify("JSON error copied to clipboard!", title="Copied")
            return

        if not self.payload:
            return

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
        if btn.disabled:
            return
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
        """Clear the payload and resume polling."""
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
        
        pyperclip.copy("") # Clear clipboard so we don't re-trigger immediately
        self.polling_timer.resume()

def copy_to_clipboard(text):
    """
    Copies text to the clipboard using pyperclip.
    """
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        console.print(f"[bold red]Error copying to clipboard:[/bold red] {e}")
        return False

def copy_file_to_clipboard(filepath: str) -> bool:
    """
    Copies a file to the clipboard using PowerShell so it can be pasted as an attachment.
    """
    try:
        abs_path = os.path.abspath(filepath)
        subprocess.run(["powershell", "-command", f"Set-Clipboard -Path '{abs_path}'"], check=True)
        return True
    except Exception as e:
        console.print(f"[bold red]Error copying file to clipboard:[/bold red] {e}")
        return False

def get_files_recursive(directory, current_depth, max_depth, extensions, exclude_dirs=None):
    """
    Recursively scans for files up to a certain depth.
    Returns a list of absolute file paths.
    
    :param extensions: A list of file extensions to filter by (e.g., ['.py', '.txt']). 
                       If None or empty, returns all files.
    :param exclude_dirs: A list or set of directory names to exclude.
    """
    file_list = []
    
    try:
        # Sort items to ensure consistent order
        items = sorted(os.listdir(directory))
    except PermissionError:
        return []

    files = []
    dirs = []

    ignore_dirs = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".idea", ".vscode"}
    if exclude_dirs:
        ignore_dirs.update(exclude_dirs)
    for item in items:
        full_path = os.path.join(directory, item)
        if os.path.isdir(full_path) and item in ignore_dirs:
            continue
        if os.path.isfile(full_path):
            files.append(full_path)
        elif os.path.isdir(full_path):
            dirs.append(full_path)

    # 1. Process files in current directory
    for f_path in files:
        if extensions:
            # endswith accepts a tuple of strings for checking multiple possibilities
            if not f_path.lower().endswith(tuple(extensions)):
                continue
        file_list.append(f_path)

    # 2. Recurse deeper if allowed
    if current_depth < max_depth:
        for d_path in dirs:
            file_list.extend(get_files_recursive(d_path, current_depth + 1, max_depth, extensions, exclude_dirs))
            
    return file_list

def generate_tree_string(files, root_dir):
    """
    Generates a string representation of the directory tree based on a list of absolute file paths.
    """
    tree_dict = {}
    for f in files:
        rel_path = os.path.relpath(f, root_dir).replace("\\", "/")
        parts = rel_path.split("/")
        current = tree_dict
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
    
    lines =[]
    def _build_lines(node, prefix=""):
        entries = sorted(list(node.keys()))
        for i, key in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + key)
            if node[key]:
                extension = "    " if is_last else "│   "
                _build_lines(node[key], prefix + extension)
    _build_lines(tree_dict)
    return "\n".join(lines)

def print_auto_summary(result: dict) -> None:
    """
    Prints a rich-formatted summary of the auto-agent execution.
    """
    if not result:
        return
        
    console.print()
    console.print(Rule("[bold blue]AI Agent Execution Summary[/bold blue]"))
    
    files = result.get("files", [])
    commit_msg = result.get("commit_message", "No commit message")
    
    applied_files = [f for f in files if f.get("_status") == "applied"]
    
    if not applied_files:
        console.print(Panel("No files were applied.", title="Result", style="bold yellow"))
        return
        
    for f in applied_files:
        action = f.get("action", "modify").lower()
        path = f.get("path", "Unknown")
        
        if action == "create":
            console.print(f"  [bold green]✓[/bold green] [green]Created File[/green] [bold]{path}[/bold]")
        elif action == "delete":
            console.print(f"  [bold red]✗[/bold red] [red]Deleted File[/red] [bold]{path}[/bold]")
        else:
            added = f.get("_added", 0)
            removed = f.get("_removed", 0)
            diff_str = f" [bold green]+{added}[/bold green] [bold red]-{removed}[/bold red]" if (added > 0 or removed > 0) else ""
            console.print(f"  [bold yellow]✓[/bold yellow] [yellow]Modified File[/yellow] [bold]{path}[/bold]{diff_str}")
            
    console.print()
    console.print(f"  [bold cyan]Committed with message:[/bold cyan] {commit_msg}")
    console.print(Rule("[bold green]Done[/bold green]"))

def display_summary(root_dir, max_depth, extensions, batch_count, total_files):
    """
    Prints a pretty table summary of the job.
    """
    table = Table(title="Job Configuration", box=box.ROUNDED)

    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")

    # Format extensions for display
    if extensions:
        ext_str = ", ".join(extensions)
    else:
        ext_str = "All (*.*)"

    table.add_row("Root Directory", root_dir)
    table.add_row("Max Depth", str(max_depth))
    table.add_row("File Extensions", ext_str)
    table.add_row("Batches", str(batch_count))
    table.add_row("Total Files Found", f"[bold green]{total_files}[/bold green]")

    console.print(table)

class ConfirmCopyApp(App):
    """TUI for confirming large copy operations."""

    CSS = """
    Screen {
        align: center middle;
        background: #2d2825;
    }
    #dialog {
        padding: 1 2;
        border: solid #d08c60;
        background: #3c3431;
        width: auto;
        height: auto;
    }
    .title {
        text-align: center;
        text-style: bold;
        color: #ead6c9;
        margin-bottom: 1;
    }
    .buttons {
        align: center middle;
        height: 3;
        margin-top: 1;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, file_count: int):
        super().__init__()
        self.file_count = file_count

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"About to copy {self.file_count} files. Are you sure?", classes="title")
            with Horizontal(classes="buttons"):
                yield Button("Yes (y)", id="btn-yes", variant="success")
                yield Button("No (n)", id="btn-no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.action_confirm()
        elif event.button.id == "btn-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.exit(True)

    def action_cancel(self) -> None:
        self.exit(False)


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
    #left-pane ListView { height: 1fr; }
    #left-pane ListItem { height: auto; }
    
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
                yield ListView(*[ListItem(Label(f)) for f in rel_files])
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

def main():
    parser = argparse.ArgumentParser(description="Scan folder and combine file contents to clipboard.")
    
    parser.add_argument("-l", "--limit", type=int, default=100, help="Max recursion depth")
    parser.add_argument("specific_file", nargs='?', help="Specific file to copy (bypasses directory scan)")
    # nargs='+' allows multiple arguments (e.g., -f py js txt)
    parser.add_argument("-f", "--file_types", nargs='+', default=None, help="File extension filters (separated by space)")
    parser.add_argument("-b", "--batches", type=int, default=1, help="Number of batches")
    parser.add_argument("-e", "--exclude", nargs='+', default=None, help="Directory names to exclude from scan (separated by space)")
    parser.add_argument("-s", "--select", action="store_true", help="Open TUI to pick files interactively")
    parser.add_argument("-a", "--auto", action="store_true", help="Run in continuous AI listener mode")
    parser.add_argument("-r", "--revert", action="store_true", help="Run in continuous AI listener mode but reverse all changes")
    parser.add_argument("-o", "--orchestrate", action="store_true", help="Run in orchestrator mode to generate a precise execution plan and prompt.")
    parser.add_argument("--web", action="store_true", help="Enable web macro mode. Translates applies into simulated keyboard strokes for web IDEs.")
    parser.add_argument("--json-select", action="store_true", help="Open TUI to pick files via JSON list on clipboard")
    parser.add_argument("--full", action="store_true", help="Run the full workflow: Select -> System -> JSON Culling -> Auto")
    parser.add_argument("--system", nargs='?', const='DEFAULT', default=None, help="Inject system prompt and user instructions. Optionally provide a path to a custom system prompt file.")
    parser.add_argument("--file-cull", action="store_true", help="Include file culling functionality in the system prompt")
    parser.add_argument("--file", action="store_true", help="Save prompt to a temp file and copy the file to clipboard")
    
    args = parser.parse_args()

    # --- Configuration ---
    root_dir = os.getcwd()
    max_depth = args.limit
    batch_count = args.batches
    zip_path_to_cleanup = None

    if args.web and not KEYBOARD_AVAILABLE:
        console.print("[bold red]Error:[/bold red] The '--web' flag requires the 'keyboard' module.")
        console.print("Please install it using: [cyan]pip install keyboard[/cyan]")
        sys.exit(1)

    # Handle specific_file being a .zip file
    if args.specific_file and args.specific_file.lower().endswith('.zip'):
        zip_path_to_cleanup = os.path.abspath(args.specific_file)
        temp_dir = tempfile.mkdtemp(prefix="combineCopy_zip_")
        atexit.register(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        
        console.print(f"[bold cyan]Extracting {args.specific_file} to temporary directory...[/bold cyan]")
        try:
            with zipfile.ZipFile(args.specific_file, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Flatten root if zip contains a single top-level directory
            extracted_items = os.listdir(temp_dir)
            if len(extracted_items) == 1 and os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
                root_dir = os.path.join(temp_dir, extracted_items[0])
            else:
                root_dir = temp_dir
                
            args.specific_file = None  # Clear this so we process the extracted dir normally
        except Exception as e:
            console.print(f"[bold red]Failed to extract zip file: {e}[/bold red]")
            sys.exit(1)

    # This list will hold the definitive set of files for context
    all_known_files = []

    # If -a, -r, or -o is standalone, launch immediately and exit before scanning/batching.
    if (args.auto or args.revert or args.orchestrate) and not (args.select or args.file_types or args.specific_file or args.system is not None or args.full or args.json_select):
        if args.orchestrate:
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
            return
        else:
            app = AutoAgentApp(root_dir, revert_mode=args.revert, web_mode=args.web)
            result = app.run()
            if result:
                print_auto_summary(result)
            return
    
    # Process extension list
    ext_filters = args.file_types
    if ext_filters:
        # Normalize: ensure lowercase and add dot if missing (e.g., "py" -> ".py")
        normalized_exts = []
        for ext in ext_filters:
            if not ext.startswith("."):
                normalized_exts.append(f".{ext.lower()}")
            else:
                normalized_exts.append(ext.lower())
        ext_filters = normalized_exts

    separator = "-" * 35

    try:
        console.print(Rule("[bold blue]CombineCopy Tool[/bold blue]"))
    
        # --- Phase 1: Scan ---
        found_files = []
        
        if args.specific_file:
            # Single file mode
            target_path = os.path.abspath(args.specific_file)
            if os.path.isfile(target_path):
                found_files = [target_path]
                console.print(f"[green]Targeting specific file:[/green] {args.specific_file}")
                
                # If the file is not inside the current root_dir (CWD), 
                # update root_dir so the relative path display looks correct.
                if not target_path.startswith(root_dir):
                    root_dir = os.path.dirname(target_path)
            else:
                console.print(Panel(f"File not found: {args.specific_file}", title="Error", style="bold red"))
                return
        else:
            # Recursive directory scan mode
            with console.status("[bold green]Scanning directory structure...[/bold green]", spinner="dots"):
                # Artificial sleep just so you can see the pretty spinner if scan is too fast
                # time.sleep(0.5) 
                found_files = get_files_recursive(root_dir, 0, max_depth, ext_filters, exclude_dirs=args.exclude)
        
        all_known_files = list(found_files) # Start with all found files

        # --- Phase 1: Manual Selection (Interactive or Full) ---
        if (args.select or args.full) and found_files:
            console.print("[bold cyan]Phase: Manual File Selection[/bold cyan]")
            selected = run_file_selector(root_dir, found_files)
            if selected is None:
                console.print(Panel("Selection cancelled.", title="Cancelled", style="bold yellow"))
                return
            found_files = selected
            all_known_files = list(selected) # After selection, this is our definitive context
    
        total_files = len(found_files)
    
        if total_files == 0:
            console.print(Panel("No matching files found.", title="Result", style="bold red"))
            return

        # --- Phase 1b: Large Copy Confirmation ---
        is_targeted = args.select or args.full or args.specific_file or args.json_select
        if total_files > 250 and not is_targeted:
            app = ConfirmCopyApp(total_files)
            confirmed = app.run()
            if not confirmed:
                console.print(Panel("Large copy operation cancelled.", title="Cancelled", style="bold yellow"))
                return
    
        display_summary(root_dir, max_depth, ext_filters, batch_count, total_files)
    
        # --- Phase 2: System Prompt & Instruction (System flag or Full) ---
        user_request_data = None
        if args.system is not None or args.full:
            console.print("[bold cyan]Phase: Instruction & System Prompt[/bold cyan]")
            sys_arg = args.system if args.system else 'DEFAULT'
            if sys_arg == 'DEFAULT' or sys_arg == '':
                if args.orchestrate:
                    sys_prompt_text = ORCHESTRATE_SYSTEM_PROMPT_TEMPLATE.strip()
                else:
                    sys_prompt_text = DEFAULT_SYSTEM_PROMPT_TEMPLATE.strip()
                    
                if args.file_cull:
                    sys_prompt_text = sys_prompt_text.replace('{FILE_CULLING_INSTRUCTION}', FILE_CULLING_PROMPT)
                else:
                    sys_prompt_text = sys_prompt_text.replace('{FILE_CULLING_INSTRUCTION}\n', '').replace('{FILE_CULLING_INSTRUCTION}', '')
            else:
                try:
                    with open(sys_arg, 'r', encoding='utf-8') as f:
                        sys_prompt_text = f.read().strip()
                except Exception as e:
                    console.print(f"[red]Error reading system prompt file: {e}[/red]")
                    return
                    
            app = SystemPromptApp(root_dir, found_files, sys_prompt_text)
            user_request_data = app.run()
            if not user_request_data:
                console.print(Panel("System prompt setup cancelled.", title="Cancelled", style="bold yellow"))
                return

        # --- Phase 3: AI File Culling / Repo Map (Full Mode) ---
        if args.full and user_request_data:
            console.print("\n[bold cyan]Phase: AI File Culling (Repo Map)[/bold cyan]")
            repo_tree = generate_tree_string(found_files, root_dir)
            
            cull_prompt = (
                f"--- SYSTEM INSTRUCTIONS ---\n"
                f"{user_request_data['system']}\n\n"
                f"--- USER REQUEST ---\n"
                f"{user_request_data['request']}\n\n"
                f"--- DIRECTORY TREE ---\n"
                f"{repo_tree}\n\n"
                f"Please reply with ONLY a JSON array of strings representing the file paths you need to view fully to complete this task. Example:[\"main.py\", \"src/utils.py\"]"
            )
            
            if copy_to_clipboard(cull_prompt):
                console.print(Panel(
                    "[bold green]Culling Prompt copied to clipboard![/bold green]\n"
                    "Paste this to the LLM. Once it replies with the JSON list of files, copy that JSON to your clipboard.",
                    border_style="green"
                ))
            
            cull_app = JsonFileSelector(root_dir)
            selected_json_files = cull_app.run()
            if selected_json_files is None:
                console.print(Panel("JSON Culling cancelled.", title="Cancelled", style="bold yellow"))
                return
            found_files = selected_json_files
            all_known_files = list(selected_json_files)
            total_files = len(found_files)
            if total_files == 0:
                console.print(Panel("No matching files found after culling.", title="Result", style="bold red"))
                return

        # --- Phase 3b: JSON Selection / File Culling (Standalone json-select) ---
        elif args.json_select and not args.full:
            console.print("[bold cyan]Phase: File Culling (JSON Selection)[/bold cyan]")
            cull_app = JsonFileSelector(root_dir)
            selected_json_files = cull_app.run()
            if selected_json_files is None:
                console.print(Panel("JSON Culling cancelled.", title="Cancelled", style="bold yellow"))
                return
            found_files = selected_json_files
            all_known_files = list(selected_json_files)
            total_files = len(found_files)
            if total_files == 0:
                console.print(Panel("No matching files found after culling.", title="Result", style="bold red"))
                return

        # --- Phase 4: Batch Calculation ---
        files_per_batch = math.ceil(total_files / batch_count)
        console.print(f"\n[dim]Splitting into {batch_count} batch(es). ~{files_per_batch} files/batch.[/dim]\n")
    
        # --- Phase 3: Process Batches ---
        for i in range(batch_count):
            batch_num = i + 1
            
            start_index = i * files_per_batch
            end_index = start_index + files_per_batch
            current_batch_files = found_files[start_index:end_index]
            
            if not current_batch_files:
                break
    
            content_buffer = []
            
            if batch_num == 1 and user_request_data:
                content_buffer.append("--- USER REQUEST ---")
                content_buffer.append(user_request_data["request"])
                content_buffer.append("\n--- SYSTEM INSTRUCTIONS ---")
                content_buffer.append(user_request_data["system"])
                content_buffer.append("\n--- USER REQUEST ---")
                content_buffer.append(user_request_data["request"])
                content_buffer.append("\n--- FILE CONTEXT ---")
    
            console.print(Rule(f"[bold yellow]Batch {batch_num}/{batch_count}[/bold yellow]"))
    
            stop_event = threading.Event()

            # Progress bar for the current batch
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                task = progress.add_task(f"[cyan]Processing {len(current_batch_files)} files (Press Ctrl+C to cancel)...", total=len(current_batch_files))

                def worker_fn():
                    for file_path in current_batch_files:
                        if stop_event.is_set():
                            return
                        
                        rel_path = os.path.relpath(file_path, root_dir)
                        progress.console.print(f"  [green]✓[/green] Adding [bold]{rel_path}[/bold]")

                        # Get extension for syntax highlighting hint (e.g., py, js, txt)
                        _, ext = os.path.splitext(rel_path)
                        lang = ext.lstrip('.').lower()

                        content_buffer.append(separator)
                        content_buffer.append(f"FILE: {rel_path}")
                        content_buffer.append(separator)
                        content_buffer.append(f"```{lang}")

                        try:
                            content_buffer.append(safe_read_file(file_path))
                        except Exception as e:
                            progress.console.print(f"  [red]![/red] Error reading {rel_path}: {e}")
                            content_buffer.append(f"[Error reading file: {e}]")
                        
                        content_buffer.append("```")
                        content_buffer.append("\n")
                        progress.advance(task)

                    if stop_event.is_set():
                        return

                    if batch_num == batch_count and user_request_data:
                        content_buffer.append("--- USER REQUEST (Reminder) ---")
                        content_buffer.append(user_request_data["request"])
                        content_buffer.append("\n--- SYSTEM REMINDER ---")
                        content_buffer.append("CRITICAL: You must ALWAYS start in PLANNING mode.")
                        content_buffer.append("Do NOT output EXECUTION or ORCHESTRATION JSON yet.")
                        content_buffer.append("When you enter PLANNING mode, do NOT wrap the implementation plan in markdown code blocks. In EXECUTION mode, you MUST wrap the JSON output in a markdown code block (```json).")
                        content_buffer.append("Create an implementation plan and wait for the user to review and approve it.")
                        content_buffer.append("When in EXECUTION mode, your commit message in the JSON payload MUST strictly adhere to this exact multi-line template structure:")
                        content_buffer.append("type(scope) : description")
                        content_buffer.append("extra desc")
                        content_buffer.append(" extra desc")
                        content_buffer.append("\n")

                    # Copy to clipboard
                    full_text = "\n".join(content_buffer)
                    if args.file:
                        try:
                            fd, temp_path = tempfile.mkstemp(prefix="combineCopy_prompt_", suffix=".txt", text=True)
                            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                                f.write(full_text)
                            if copy_file_to_clipboard(temp_path):
                                progress.console.print(Panel(
                                    f"[bold green]Batch {batch_num} saved to {temp_path} and copied to clipboard![/bold green]\n"
                                    f"Contains {len(current_batch_files)} files.",
                                    border_style="green"
                                ))
                        except Exception as e:
                            progress.console.print(f"[bold red]Failed to save/copy file:[/bold red] {e}")
                    else:
                        if copy_to_clipboard(full_text):
                            progress.console.print(Panel(
                                f"[bold green]Batch {batch_num} copied to clipboard![/bold green]\n"
                                f"Contains {len(current_batch_files)} files.",
                                border_style="green"
                            ))

                worker_thread = threading.Thread(target=worker_fn, daemon=True)
                worker_thread.start()

                try:
                    while worker_thread.is_alive():
                        worker_thread.join(0.1)
                except KeyboardInterrupt:
                    stop_event.set()
                    raise KeyboardInterrupt

            if stop_event.is_set():
                break
    
            # Pause Logic
            if batch_num < batch_count and end_index < total_files:
                console.print("[bold white on blue] PAUSE [/bold white on blue] Paste content now, then press [bold]Enter[/bold] for next batch...")
                input()
                console.print() # New line
            else:
                console.print(Rule("[bold green]All Done[/bold green]"))
                
    except KeyboardInterrupt:
        console.print()
        console.print(Panel("[bold red]Process interrupted by user (Ctrl+C).[/bold red]", title="Cancelled"))
        return
        
    # --- Final Phase: Auto Agent ---
    if args.auto or args.full or args.revert or args.orchestrate:
        if args.orchestrate:
            console.print(f"\n[bold cyan]Phase: Orchestrator Agent Execution[/bold cyan]")
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
        else:
            phase_name = "Auto Agent Execution (Revert Mode)" if args.revert else "Auto Agent Execution"
            if args.web:
                phase_name += " [WEB MACRO MODE]"
                
            console.print(f"\n[bold cyan]Phase: {phase_name}[/bold cyan]")
            # Use ignore_initial_clipboard=True because we just copied the context to the clipboard ourselves.
            app = AutoAgentApp(root_dir, all_known_files, revert_mode=args.revert, ignore_initial_clipboard=True, web_mode=args.web)
            result = app.run()
            if result:
                print_auto_summary(result)

    # --- Cleanup Phase ---
    if zip_path_to_cleanup and os.path.exists(zip_path_to_cleanup):
        console.print()
        ans = console.input(f"[bold yellow]Delete the source .zip file ({os.path.basename(zip_path_to_cleanup)})? [Y/n]: [/bold yellow]").strip().lower()
        if ans in ['', 'y', 'yes']:
            try:
                os.remove(zip_path_to_cleanup)
                console.print(f"[green]Successfully deleted {os.path.basename(zip_path_to_cleanup)}[/green]")
            except Exception as e:
                console.print(f"[red]Failed to delete {zip_path_to_cleanup}: {e}[/red]")

if __name__ == "__main__":
    main()
