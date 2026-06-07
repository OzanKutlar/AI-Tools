import os
import argparse
import math
import time
import threading
import sys
import tempfile
import zipfile
import atexit
import shutil
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.rule import Rule

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

from cc_utils import (
    console,
    safe_read_file,
    get_files_recursive,
    generate_tree_string,
    print_auto_summary,
    display_summary,
    copy_to_clipboard,
    copy_file_to_clipboard
)

from cc_prompts import (
    ORCHESTRATE_SYSTEM_PROMPT_TEMPLATE,
    DEFAULT_SYSTEM_PROMPT_TEMPLATE,
    CLI_SYSTEM_PROMPT_TEMPLATE
)

from tui_selection import run_file_selector
from tui_prompt import SystemPromptApp
from tui_confirm import ConfirmCopyApp
from tui_apply import AutoAgentApp, OrchestratorAgentApp
from tui_kanban import KanbanApp

def main():
    parser = argparse.ArgumentParser(description="Scan folder and combine file contents to clipboard.")
    parser.add_argument("-l", "--limit", type=int, default=100, help="Max recursion depth")
    parser.add_argument("specific_file", nargs='?', help="Specific file to copy (bypasses directory scan)")
    parser.add_argument("-f", "--file_types", nargs='+', default=None, help="File extension filters (separated by space)")
    parser.add_argument("-b", "--batches", type=int, default=1, help="Number of batches")
    parser.add_argument("-e", "--exclude", nargs='+', default=None, help="Directory names to exclude from scan (separated by space)")
    parser.add_argument("-s", "--select", action="store_true", help="Open TUI to pick files interactively")
    parser.add_argument("-a", "--auto", action="store_true", help="Run in continuous AI listener mode")
    parser.add_argument("-r", "--revert", action="store_true", help="Run in continuous AI listener mode but reverse all changes")
    parser.add_argument("-o", "--orchestrate", action="store_true", help="Run in orchestrator mode to generate a precise execution plan and prompt.")
    parser.add_argument("--cli", action="store_true", help="Enable CLI Mode. Allows the AI to output terminal commands to be executed.")
    parser.add_argument("--web", action="store_true", help="Launch the local web UI server.")
    parser.add_argument("--web-apply", action="store_true", dest="web_apply", help="Enable web macro mode. Translates applies into simulated keyboard strokes for web IDEs.")
    parser.add_argument("--tfs", action="store_true", help="Use TFVC (tf.exe) instead of git for checkout and checkin operations.")
    parser.add_argument("--system", nargs='?', const='DEFAULT', default=None, help="Inject system prompt and user instructions. Optionally provide a path to a custom system prompt file.")
    parser.add_argument("--file", action="store_true", help="Save prompt to a temp file and copy the file to clipboard")
    parser.add_argument("-k", "--kanban", action="store_true", help="Launch the persistent Kanban board interface")
    parser.add_argument("--file-culling", "--file-cull", action="store_true", dest="file_culling", help="Enable file culling / AST selection mode")
    parser.add_argument("-js", "--json-select", action="store_true", help="Parse a JSON selection payload from clipboard to automatically select files/functions")
    args = parser.parse_args()

    root_dir = os.getcwd()
    max_depth = args.limit
    batch_count = args.batches
    zip_path_to_cleanup = None

    if args.web_apply and not KEYBOARD_AVAILABLE:
        console.print("[bold red]Error:[/bold red] The '--web-apply' flag requires the 'keyboard' module.")
        console.print("Please install it using: [cyan]pip install keyboard[/cyan]")
        sys.exit(1)

    if args.specific_file and args.specific_file.lower().endswith('.zip'):
        zip_path_to_cleanup = os.path.abspath(args.specific_file)
        temp_dir = tempfile.mkdtemp(prefix="combineCopy_zip_")
        atexit.register(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        
        console.print(f"[bold cyan]Extracting {args.specific_file} to temporary directory...[/bold cyan]")
        try:
            with zipfile.ZipFile(args.specific_file, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            extracted_items = os.listdir(temp_dir)
            if len(extracted_items) == 1 and os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
                root_dir = os.path.join(temp_dir, extracted_items[0])
            else:
                root_dir = temp_dir
            args.specific_file = None
        except Exception as e:
            console.print(f"[bold red]Failed to extract zip file: {e}[/bold red]")
            sys.exit(1)

    all_known_files = []

    if args.kanban:
        app = KanbanApp(root_dir, cli_mode=args.cli)
        logs = app.run()
        if logs:
            console.print()
            console.print(Rule("[bold blue]Kanban Session Summary[/bold blue]"))
            for log_msg in logs:
                console.print(f"  • {log_msg}")
            console.print()
        return

    if (args.auto or args.revert or args.orchestrate) and not (args.select or args.file_types or args.specific_file or args.system is not None or args.cli):
        if args.orchestrate:
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file, cli_mode=args.cli)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
            return
        else:
            app = AutoAgentApp(root_dir, revert_mode=args.revert, web_mode=args.web, tfs_mode=args.tfs)
            result = app.run()
            if result:
                print_auto_summary(result)
            return
    
    ext_filters = args.file_types
    if ext_filters:
        normalized_exts = []
        for ext in ext_filters:
            if not ext.startswith("."):
                normalized_exts.append(f".{ext.lower()}")
            else:
                normalized_exts.append(ext.lower())
        ext_filters = normalized_exts

    if args.web:
        from web_server import start_server
        console.print("\n[bold green]Starting CombineCopy Web UI...[/bold green]")
        console.print("Access it in your browser at: [bold cyan]http://127.0.0.1:5000[/bold cyan]\n")
        start_server(root_dir, max_depth, ext_filters, args.exclude)
        return

    if ext_filters:
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
        found_files = []
        important_files = None
        partial_files = {}

        if args.json_select:
            console.print("[bold cyan]Phase: JSON Selection Parsing[/bold cyan]")
            import pyperclip
            try:
                clipboard_content = pyperclip.paste().strip()
            except Exception as e:
                console.print(f"[bold red]Error reading clipboard:[/bold red] {e}")
                return
            
            if not clipboard_content:
                console.print("[bold red]Clipboard is empty.[/bold red]")
                return

            results = []
            idx = 0
            while idx < len(clipboard_content):
                start_idx = clipboard_content.find('{', idx)
                if start_idx == -1:
                    break
                depth = 0
                in_string = False
                escape_next = False
                end_idx = -1
                for i in range(start_idx, len(clipboard_content)):
                    char = clipboard_content[i]
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
                    results.append(clipboard_content[start_idx:end_idx + 1])
                    idx = end_idx + 1
                else:
                    idx = start_idx + 1

            selection_data = None
            for json_str in results:
                from cc_utils import intelligent_json_fix
                data, _ = intelligent_json_fix(json_str)
                if data and isinstance(data, dict):
                    if data.get("phase") == "SELECT" or "files" in data or "functions" in data:
                        selection_data = data
                        break

            if not selection_data:
                console.print("[bold red]No valid SELECT JSON payload found on clipboard.[/bold red]")
                return

            console.print("[green]Found valid JSON selection payload.[/green]")
            found_files = []
            important_files = []
            partial_files = {}

            full_files_list = selection_data.get("files", [])
            for f in full_files_list:
                abs_path = os.path.abspath(os.path.join(root_dir, f))
                if os.path.exists(abs_path) and os.path.isfile(abs_path):
                    found_files.append(abs_path)
                    important_files.append(abs_path)
                    console.print(f"  Selected full file: [cyan]{f}[/cyan]")
                else:
                    console.print(f"  [yellow]Warning:[/yellow] File not found: [red]{f}[/red]")

            functions_list = selection_data.get("functions", [])
            for entry in functions_list:
                fpath = entry.get("path")
                names = entry.get("names", [])
                if not fpath:
                    continue
                abs_path = os.path.abspath(os.path.join(root_dir, fpath))
                if os.path.exists(abs_path) and os.path.isfile(abs_path):
                    content = safe_read_file(abs_path)
                    from cc_utils import extract_blocks
                    blocks = extract_blocks(abs_path, content)
                    matched_blocks = []
                    for name in names:
                        found_block = False
                        for b in blocks:
                            if name in b["name"]:
                                matched_blocks.append(b)
                                found_block = True
                        if not found_block:
                            console.print(f"  [yellow]Warning:[/yellow] Function/Class '[red]{name}[/red]' not found in [cyan]{fpath}[/cyan]")
                    
                    if matched_blocks:
                        found_files.append(abs_path)
                        partial_files[abs_path] = matched_blocks
                        console.print(f"  Selected functions from [cyan]{fpath}[/cyan]: {', '.join(names)}")
                else:
                    console.print(f"  [yellow]Warning:[/yellow] File not found for partial selection: [red]{fpath}[/red]")

            if not found_files:
                console.print("[bold red]No files were successfully selected from the JSON payload.[/bold red]")
                return

            all_known_files = list(found_files)

        else:
            if args.specific_file:
                target_path = os.path.abspath(args.specific_file)
                if os.path.isfile(target_path):
                    found_files = [target_path]
                    important_files = [target_path]
                    console.print(f"[green]Targeting specific file:[/green] {args.specific_file}")
                    if not target_path.startswith(root_dir):
                        root_dir = os.path.dirname(target_path)
                else:
                    console.print(Panel(f"File not found: {args.specific_file}", title="Error", style="bold red"))
                    return
            else:
                with console.status("[bold green]Scanning directory structure...[/bold green]", spinner="dots"):
                    found_files = get_files_recursive(root_dir, 0, max_depth, ext_filters, exclude_dirs=args.exclude)
            
            all_known_files = list(found_files)

            partial_files = {}
            if args.select and found_files:
                console.print("[bold cyan]Phase: Manual File Selection[/bold cyan]")
                selected = run_file_selector(root_dir, found_files, ast_mode=args.file_culling)
                if selected is None:
                    console.print(Panel("Selection cancelled.", title="Cancelled", style="bold yellow"))
                    return
                found_files, important_files, partial_files = selected
                all_known_files = list(found_files)
            else:
                if important_files is None:
                    important_files = list(found_files)
    
        total_files = len(found_files)
        if total_files == 0:
            console.print(Panel("No matching files found.", title="Result", style="bold red"))
            return

        is_targeted = args.select or args.specific_file
        if total_files > 250 and not is_targeted:
            app = ConfirmCopyApp(total_files)
            confirmed = app.run()
            if not confirmed:
                console.print(Panel("Large copy operation cancelled.", title="Cancelled", style="bold yellow"))
                return
    
        display_summary(root_dir, max_depth, ext_filters, batch_count, total_files)
    
        user_request_data = None
        if args.system is not None or args.cli:
            console.print("[bold cyan]Phase: Instruction & System Prompt[/bold cyan]")
            sys_arg = args.system if args.system else 'DEFAULT'
            if sys_arg == 'DEFAULT' or sys_arg == '':
                if args.orchestrate:
                    sys_prompt_text = ORCHESTRATE_SYSTEM_PROMPT_TEMPLATE.strip()
                elif args.cli:
                    sys_prompt_text = CLI_SYSTEM_PROMPT_TEMPLATE.strip()
                else:
                    sys_prompt_text = DEFAULT_SYSTEM_PROMPT_TEMPLATE.strip()
            else:
                try:
                    with open(sys_arg, 'r', encoding='utf-8') as f:
                        sys_prompt_text = f.read().strip()
                except Exception as e:
                    console.print(f"[red]Error reading system prompt file: {e}[/red]")
                    return
            
            if args.file_culling:
                from cc_prompts import FILE_CULLING_INSTRUCTIONS
                sys_prompt_text += "\n\n" + FILE_CULLING_INSTRUCTIONS.strip()
                    
            app = SystemPromptApp(root_dir, found_files, sys_prompt_text)
            user_request_data = app.run()
            if not user_request_data:
                console.print(Panel("System prompt setup cancelled.", title="Cancelled", style="bold yellow"))
                return

        files_per_batch = math.ceil(total_files / batch_count)
        console.print(f"\n[dim]Splitting into {batch_count} batch(es). ~{files_per_batch} files/batch.[/dim]\n")
    
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
                
                ast_tree = generate_tree_string(found_files, root_dir)
                content_buffer.append("\n--- DIRECTORY AST MAP ---")
                content_buffer.append(ast_tree)
                
                content_buffer.append("\n--- FILE CONTEXT ---")
    
            console.print(Rule(f"[bold yellow]Batch {batch_num}/{batch_count}[/bold yellow]"))
            stop_event = threading.Event()

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"[cyan]Processing {len(current_batch_files)} files (Press Ctrl+C to cancel)...", total=len(current_batch_files))

                def worker_fn():
                    important_set = set(important_files) if important_files is not None else set()
                    for file_path in current_batch_files:
                        if stop_event.is_set(): return
                        if file_path in important_set or file_path in partial_files:
                            rel_path = os.path.relpath(file_path, root_dir)
                            is_partial = file_path in partial_files and file_path not in important_set
                            
                            if is_partial:
                                progress.console.print(f"  [green]✓[/green] Adding [bold]{rel_path}[/bold] (Partial Context)")
                            else:
                                progress.console.print(f"  [green]✓[/green] Adding [bold]{rel_path}[/bold] (Full Context)")
                                
                            _, ext = os.path.splitext(rel_path)
                            lang = ext.lstrip('.').lower()
                            content_buffer.append(separator)
                            content_buffer.append(f"FILE: {rel_path}")
                            content_buffer.append(separator)
                            content_buffer.append(f"```{lang}")
                            try:
                                content = safe_read_file(file_path)
                                if is_partial:
                                    blocks = partial_files[file_path]
                                    lines = content.splitlines(keepends=True)
                                    intervals = []
                                    for b in blocks:
                                        intervals.append([max(0, b["start"] - 3), min(len(lines) - 1, b["end"] + 3)])
                                    intervals.sort(key=lambda x: x[0])
                                    merged = []
                                    for interval in intervals:
                                        if not merged or merged[-1][1] < interval[0] - 1:
                                            merged.append(interval)
                                        else:
                                            merged[-1][1] = max(merged[-1][1], interval[1])
                                    partial_content = []
                                    for i, interval in enumerate(merged):
                                        if i > 0:
                                            partial_content.append("\n// ... (hidden lines) ...\n\n")
                                        partial_content.extend(lines[interval[0]:interval[1]+1])
                                    content_buffer.append("".join(partial_content))
                                else:
                                    content_buffer.append(content)
                            except Exception as e:
                                progress.console.print(f"  [red]![/red] Error reading {rel_path}: {e}")
                                content_buffer.append(f"[Error reading file: {e}]")
                            content_buffer.append("```")
                            content_buffer.append("\n")
                        else:
                            rel_path = os.path.relpath(file_path, root_dir)
                            progress.console.print(f"  [cyan]ℹ[/cyan] Included [bold]{rel_path}[/bold] in AST Map")
                        progress.advance(task)
                    if stop_event.is_set(): return
                    if batch_num == batch_count and user_request_data:
                        content_buffer.append("--- USER REQUEST (Reminder) ---")
                        content_buffer.append(user_request_data["request"])
                        content_buffer.append("\n--- SYSTEM REMINDER ---")
                        content_buffer.append("CRITICAL: You must ALWAYS start in PLANNING mode.")
                        content_buffer.append("Do NOT output EXECUTION or ORCHESTRATION JSON yet.")
                        content_buffer.append("When you enter PLANNING mode, present your Implementation Plan and Task Checklist directly as standard inline markdown sections. Do NOT output them in file-formatted codeblocks and do NOT assign filenames or paths to them (e.g. do not label them as C:\\Users\\Ozan\\task.md or C:\\Users\\Ozan\\implementation_plan.md). In EXECUTION mode, you MUST wrap the JSON output in a markdown code block (```json).")
                        content_buffer.append("Create an inline implementation plan and wait for the user to review and approve it.")
                        content_buffer.append("When in EXECUTION mode, your commit message in the JSON payload MUST strictly adhere to this exact multi-line template structure:")
                        content_buffer.append("type(scope) : description")
                        content_buffer.append("extra desc")
                        content_buffer.append(" extra desc")
                        content_buffer.append("\n")
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
    
            if batch_num < batch_count and end_index < total_files:
                console.print("[bold white on blue] PAUSE [/bold white on blue] Paste content now, then press [bold]Enter[/bold] for next batch...")
                input()
                console.print()
            else:
                console.print(Rule("[bold green]All Done[/bold green]"))
                
    except KeyboardInterrupt:
        console.print()
        console.print(Panel("[bold red]Process interrupted by user (Ctrl+C).[/bold red]", title="Cancelled"))
        return
        
    if args.auto or args.revert or args.orchestrate:
        if args.orchestrate:
            console.print(f"\n[bold cyan]Phase: Orchestrator Agent Execution[/bold cyan]")
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file, cli_mode=args.cli)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
        else:
            phase_name = "Auto Agent Execution (Revert Mode)" if args.revert else "Auto Agent Execution"
            if args.web_apply:
                phase_name += " [WEB MACRO MODE]"
            console.print(f"\n[bold cyan]Phase: {phase_name}[/bold cyan]")
            app = AutoAgentApp(root_dir, all_known_files, revert_mode=args.revert, ignore_initial_clipboard=True, web_mode=args.web_apply, tfs_mode=args.tfs)
            result = app.run()
            if result:
                print_auto_summary(result)

    if zip_path_to_cleanup and os.path.exists(zip_path_to_cleanup):
        console.print()
        ans = console.input(f"[bold yellow]Delete the source .zip file ({os.path.basename(zip_path_to_cleanup)})? [Y/n]: [/bold yellow]").strip().lower()
        if ans in ['', 'y', 'yes']:
            try:
                os.remove(zip_path_to_cleanup)
                console.print(f"[green]Successfully deleted {os.path.basename(zip_path_to_cleanup)}[/green]")
            except Exception as e:
                console.print(f"[red]Failed to delete {zip_path_to_cleanup}: {e}[/red]")

def app_main():
    """Entry point for the 'app' command. Injects the '-a' flag automatically."""
    import sys
    if "-a" not in sys.argv and "--auto" not in sys.argv:
        sys.argv.insert(1, "-a")
    main()

if __name__ == "__main__":
    main()
