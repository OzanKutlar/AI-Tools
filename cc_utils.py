import os
import json
import difflib
import re
import subprocess
from rich.console import Console
from rich.table import Table
from rich import box
from rich.rule import Rule
from rich.text import Text
from rich.style import Style
import pyperclip

# Initialize Rich Console
console = Console()

def extract_blocks(filepath: str, content: str) -> list[dict]:
    """Parses content to extract blocks (classes, functions, methods) with start and end line indices."""
    ext = os.path.splitext(filepath)[1].lower()
    lines = content.split('\n')
    blocks = []

    py_pattern = re.compile(r'^\s*(class|def|async def)\s+\w+')
    js_pattern = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:class\s+\w+|function\s+\w+\s*\(|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_]+)\s*=>)')
    cs_method_pattern = re.compile(r'^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|abstract|sealed|new|final|synchronized|native|strictfp)\s+)*[\w<>\[\]\?]+\s+\w+\s*(?:<[^>]*>\s*)?\(')
    cs_ctor_pattern = re.compile(r'^\s*(?:(?:public|private|protected|internal|static)\s+)+\w+\s*\(')
    cs_class_pattern = re.compile(r'^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|sealed|partial|final|strictfp)\s+)*(?:class|struct|interface|record|enum)\s+\w+')

    cs_exclusions = {"if", "while", "for", "foreach", "switch", "catch", "using", "lock", "typeof", "sizeof", "default", "return", "throw", "new"}

    i = 0
    while i < len(lines):
        line = lines[i]
        matched = False
        name = ""
        is_brace_lang = False

        if ext in ['.py', '.pyw']:
            if py_pattern.match(line):
                matched = True
                name = line.strip()
                is_brace_lang = False
        elif ext in ['.js', '.jsx', '.ts', '.tsx']:
            if js_pattern.match(line):
                matched = True
                name = line.strip()
                is_brace_lang = True
        elif ext in ['.cs', '.java']:
            if cs_class_pattern.match(line):
                matched = True
                name = line.strip()
                is_brace_lang = True
            else:
                m = cs_method_pattern.match(line) or cs_ctor_pattern.match(line)
                if m:
                    parts = line.split('(')[0].strip().split()
                    if parts and parts[-1] not in cs_exclusions:
                        matched = True
                        name = line.strip()
                        is_brace_lang = True

        if matched:
            start_line = i
            end_line = i
            if not is_brace_lang:
                base_indent = len(line) - len(line.lstrip())
                j = i + 1
                while j < len(lines):
                    curr_line = lines[j]
                    if curr_line.strip() and not curr_line.lstrip().startswith('#'):
                        curr_indent = len(curr_line) - len(curr_line.lstrip())
                        if curr_indent <= base_indent:
                            break
                    end_line = j
                    j += 1
            else:
                brace_count = 0
                found_start = False
                j = i
                while j < len(lines):
                    curr_line = lines[j]
                    clean_line = re.sub(r'".*?(?<!\\)"', '', curr_line)
                    clean_line = re.sub(r"'.*?(?<!\\)'", '', clean_line)
                    clean_line = re.sub(r'//.*', '', clean_line)
                    
                    if not found_start:
                        if ';' in clean_line and '{' not in clean_line:
                            end_line = j
                            break
                        if '{' in clean_line:
                            found_start = True
                            brace_count += clean_line.count('{')
                            brace_count -= clean_line.count('}')
                            if brace_count <= 0:
                                end_line = j
                                break
                    else:
                        brace_count += clean_line.count('{')
                        brace_count -= clean_line.count('}')
                        if brace_count <= 0:
                            end_line = j
                            break
                    end_line = j
                    j += 1

            blocks.append({
                "name": name[:100],
                "start": start_line,
                "end": end_line
            })
        i += 1
    return blocks

def extract_signatures(filepath: str, content: str) -> list[str]:
    """Uses block extraction to retrieve class and function definitions from source code."""
    blocks = extract_blocks(filepath, content)
    return [b["name"] for b in blocks]

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
    """Reads a file as UTF-8. Falls back to surrogateescape so invalid bytes
    can round-trip losslessly when written back. Ignores binary files."""
    if is_binary_file(path):
        return "(This is a binary file)"
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
                return f.read()
        except Exception:
            return "(This is a binary file)"

def detect_newline(path: str) -> str:
    """Peeks at a file in binary mode and returns its dominant line ending.
    Returns '\\r\\n', '\\n', '\\r', or '' (no newline found)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(65536)
        if b"\r\n" in chunk:
            return "\r\n"
        if b"\n" in chunk:
            return "\n"
        if b"\r" in chunk:
            return "\r"
        return ""
    except Exception:
        return ""

def extract_json_from_text(text: str) -> list[str]:
    """Extracts JSON objects from text using multiple strategies."""
    results = []
    
    # Strategy A: Markdown code blocks
    import re
    code_blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_blocks:
        return code_blocks
        
    # Strategy B: First { to last } (Heuristic for single payload)
    if '"phase"' in text and any(k in text for k in ['"EXECUTION"', '"ORCHESTRATE"', '"EXPLORATION"', '"SELECT"']):
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            results.append(text[start_idx:end_idx+1])
            return results

    # Strategy C: State machine fallback
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

def intelligent_json_fix(content: str) -> tuple[dict | None, str]:
    """Iteratively attempts to heal common LLM JSON syntax errors (like unescaped quotes)."""
    import re
    
    def escape_html_attr(m):
        attr = m.group(1)
        val = m.group(2)
        return f'{attr}=\\"{val}\\"'

    # Pre-pass: Fix common HTML/JSX unescaped attributes (e.g., className="flex" -> className=\"flex\")
    content = re.sub(r'([a-zA-Z0-9_\-]+)\s*=\s*"(.*?)(?<!\\)"', escape_html_attr, content)
    
    def escape_conditional(m):
        op = m.group(1)
        val = m.group(2)
        return f'{op} \\"{val}\\"'

    # Pre-pass: Fix common programming conditionals/returns (e.g., == "foo" -> == \"foo\")
    content = re.sub(r'(==|===|!=|!==|\+=|-=|\*=|/=|return)\s*"(.*?)(?<!\\)"', escape_conditional, content)

    lines = content.split('\n')
    for i, line in enumerate(lines):
        match = re.match(r'^(\s*"[^"]+"\s*:\s*")(.*)$', line)
        if match:
            prefix = match.group(1)
            rest = match.group(2)
            suffix_match = re.search(r'("\s*(?:,|},|})?\s*)$', rest)
            if suffix_match:
                suffix = suffix_match.group(1)
                value = rest[:-len(suffix)]
                fixed_value = re.sub(r'(?<!\\)"', r'\"', value)
                lines[i] = prefix + fixed_value + suffix
            else:
                fixed_value = re.sub(r'(?<!\\)"', r'\"', rest)
                lines[i] = prefix + fixed_value
                
    current = '\n'.join(lines)

    for _ in range(10000):
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
                if pos > 0 and current[pos-1] == '\\':
                    current = current[:pos-1] + '\\\\' + current[pos:]
                    continue
                
            if "Expecting ',' delimiter" in msg or "Expecting value" in msg or "Extra data" in msg:
                last_quote = current.rfind('"', 0, pos)
                if last_quote != -1 and current[last_quote-1] != '\\':
                    current = current[:last_quote] + '\\"' + current[last_quote+1:]
                    continue
                    
            break
    return None, content

def render_word_diff(old_text: str, new_text: str, diff_view) -> None:
    """Calculates word-level diffs and outputs them to the rich RichLog container."""
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

def copy_to_clipboard(text: str) -> bool:
    """Copies text to the clipboard using pyperclip."""
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        console.print(f"[bold red]Error copying to clipboard:[/bold red] {e}")
        return False

def copy_file_to_clipboard(filepath: str) -> bool:
    """Copies a file to the clipboard using PowerShell so it can be pasted as an attachment."""
    try:
        abs_path = os.path.abspath(filepath)
        subprocess.run(["powershell", "-command", f"Set-Clipboard -Path '{abs_path}'"], check=True)
        return True
    except Exception as e:
        console.print(f"[bold red]Error copying file to clipboard:[/bold red] {e}")
        return False

def get_files_recursive(directory, current_depth, max_depth, extensions, exclude_dirs=None):
    """Recursively scans for files up to a certain depth."""
    file_list = []
    try:
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

    for f_path in files:
        if extensions:
            if not f_path.lower().endswith(tuple(extensions)):
                continue
        file_list.append(f_path)

    if current_depth < max_depth:
        for d_path in dirs:
            file_list.extend(get_files_recursive(d_path, current_depth + 1, max_depth, extensions, exclude_dirs))
            
    return file_list

def generate_tree_string(files, root_dir) -> str:
    """Generates a string representation of the directory tree, enriched with AST signatures."""
    tree_dict = {}
    for f in files:
        rel_path = os.path.relpath(f, root_dir).replace("\\", "/")
        parts = rel_path.split("/")
        current = tree_dict
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
            
    signatures_map = {}
    for f in files:
        content = safe_read_file(f)
        if content and not content.startswith("(This is a binary"):
            sigs = extract_signatures(f, content)
            if sigs:
                rel_path = os.path.relpath(f, root_dir).replace("\\", "/")
                signatures_map[rel_path] = sigs

    lines = []
    def _build_lines(node, prefix="", path_prefix=""):
        entries = sorted(list(node.keys()))
        for i, key in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + key)
            
            current_path = f"{path_prefix}/{key}" if path_prefix else key
            
            if not node[key]:
                sigs = signatures_map.get(current_path, [])
                extension = "    " if is_last else "│   "
                for j, sig in enumerate(sigs):
                    sig_is_last = (j == len(sigs) - 1)
                    sig_connector = "└── " if sig_is_last else "├── "
                    lines.append(prefix + extension + sig_connector + "[AST] " + sig)
            else:
                extension = "    " if is_last else "│   "
                _build_lines(node[key], prefix + extension, current_path)
    _build_lines(tree_dict)
    return "\n".join(lines)

def print_auto_summary(result: dict) -> None:
    """Prints a rich summary of modifications applied."""
    if not result:
        return
    console.print()
    console.print(Rule("[bold blue]AI Agent Execution Summary[/bold blue]"))
    files = result.get("files", [])
    commit_msg = result.get("commit_message", "No commit message")
    applied_files = [f for f in files if f.get("_status") == "applied"]
    
    if not applied_files:
        from rich.panel import Panel
        console.print(Panel("No files were applied.", title="Result", style="bold yellow"))
        return
        
    for f in applied_files:
        action = f.get("action", "modify").lower()
        if action == "command":
            command = f.get("command", "Unknown")
            console.print(f"  [bold magenta]>[/bold magenta] [magenta]Ran Command[/magenta] [bold]{command}[/bold]")
        elif action == "create":
            path = f.get("path", "Unknown")
            console.print(f"  [bold green]✓[/bold green] [green]Created File[/green] [bold]{path}[/bold]")
        elif action == "delete":
            path = f.get("path", "Unknown")
            console.print(f"  [bold red]✗[/bold red] [red]Deleted File[/red] [bold]{path}[/bold]")
        else:
            path = f.get("path", "Unknown")
            added = f.get("_added", 0)
            removed = f.get("_removed", 0)
            diff_str = f" [bold green]+{added}[/bold green] [bold red]-{removed}[/bold red]" if (added > 0 or removed > 0) else ""
            console.print(f"  [bold yellow]✓[/bold yellow] [yellow]Modified File[/yellow] [bold]{path}[/bold]{diff_str}")
            
    console.print()
    commit_hash = result.get("commit_hash")
    if commit_hash:
        console.print(f"  [bold cyan]Committed with message:[/bold cyan] {commit_msg}")
    else:
        console.print(f"  [bold yellow]Note: Changes were applied but not committed to git.[/bold yellow]")
    console.print(Rule("[bold green]Done[/bold green]"))

def display_summary(root_dir, max_depth, extensions, batch_count, total_files) -> None:
    """Prints a pretty table configuration summary."""
    table = Table(title="Job Configuration", box=box.ROUNDED)
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")
    
    ext_str = ", ".join(extensions) if extensions else "All (*.*)"
    table.add_row("Root Directory", root_dir)
    table.add_row("Max Depth", str(max_depth))
    table.add_row("File Extensions", ext_str)
    table.add_row("Batches", str(batch_count))
    table.add_row("Total Files Found", f"[bold green]{total_files}[/bold green]")
    console.print(table)
