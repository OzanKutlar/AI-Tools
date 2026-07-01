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
import random
import re
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.rule import Rule

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# Ensure the root directory is in sys.path so 'combinecopy' can be imported
# regardless of where the script is executed from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from combinecopy.utils import (
    console,
    safe_read_file,
    get_files_recursive,
    generate_tree_string,
    print_auto_summary,
    display_summary,
    copy_to_clipboard,
    copy_file_to_clipboard
)

from combinecopy.prompts import (
    get_system_prompt,
    build_prompt,
    get_user_prompt,
    get_ast,
    get_file_context,
    get_system_prompt_important
)

from combinecopy.tui.selection import run_file_selector
from combinecopy.tui.prompt import SystemPromptApp
from combinecopy.tui.confirm import ConfirmCopyApp
from combinecopy.tui.apply import AutoAgentApp, OrchestratorAgentApp

def resolve_random_paths(paths: list[str]) -> list[str]:
    resolved = []
    pattern = re.compile(r'\$\{r\((\d+),\s*(\d+)\)\}')
    for p in paths:
        match = pattern.search(p)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            
            candidates = []
            for i in range(start, end + 1):
                test_path = p.replace(match.group(0), str(i), 1)
                if os.path.exists(test_path):
                    candidates.append(test_path)
            
            if not candidates:
                console.print(Panel(f"No existing files found for range pattern in:\n{p}", title="Error", style="bold red"))
                sys.exit(1)
            
            selected = random.choice(candidates)
            console.print(f"[bold cyan]Randomly selected:[/bold cyan] {selected} [dim](from {len(candidates)} candidates)[/dim]")
            resolved.append(selected)
        else:
            resolved.append(p)
    return resolved

def main():
    parser = argparse.ArgumentParser(description="Scan folder and combine file contents to clipboard.")
    parser.add_argument("-l", "--limit", type=int, default=100, help="Max recursion depth")
    parser.add_argument("paths", nargs='*', help="Specific files or directories to include (bypasses full directory scan)")
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
    parser.add_argument("--system-only", action="store_true", help="Copy only the system prompt to the clipboard and exit.")
    parser.add_argument("--file", action="store_true", help="Save prompt to a temp file and copy the file to clipboard")
    parser.add_argument("--file-culling", "--file-cull", action="store_true", dest="file_culling", help="Enable file culling / AST selection mode")
    parser.add_argument("-js", "--json-select", action="store_true", help="Parse a JSON selection payload from clipboard to automatically select files/functions")
    parser.add_argument("-x", "--xml", action="store_true", help="Instruct the AI to use XML for payloads instead of JSON to completely avoid quote escaping issues.")
    parser.add_argument("--consult", action="store_true", help="Enable CONSULT phase for the AI to ask abstract questions to an external LLM.")
    args = parser.parse_args()

    if args.system_only:
        agent_type = "orchestrator" if args.orchestrate else "cli" if args.cli else "default"
        sys_prompt = get_system_prompt(agent_type=agent_type, file_cull=args.file_culling, xml_mode=args.xml, consult=args.consult)
        important = get_system_prompt_important(agent_type=agent_type, xml_mode=args.xml)
        
        full_sys_prompt = f"--- SYSTEM INSTRUCTIONS ---\n{sys_prompt}\n\n{important}"
        
        if args.file:
            try:
                fd, temp_path = tempfile.mkstemp(prefix="combineCopy_sysprompt_", suffix=".txt", text=True)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(full_sys_prompt)
                if copy_file_to_clipboard(temp_path):
                    console.print(Panel(f"[bold green]System prompt saved to {temp_path} and copied to clipboard![/bold green]", title="Success"))
            except Exception as e:
                console.print(f"[bold red]Failed to save/copy file:[/bold red] {e}")
        else:
            if copy_to_clipboard(full_sys_prompt):
                console.print(Panel("[bold green]System prompt copied to clipboard![/bold green]", title="Success"))
            else:
                console.print(full_sys_prompt)
        return

    if args.paths:
        args.paths = resolve_random_paths(args.paths)

    root_dir = os.getcwd()
    max_depth = args.limit
    batch_count = args.batches
    zip_path_to_cleanup = None

    if args.web_apply and not KEYBOARD_AVAILABLE:
        console.print("[bold red]Error:[/bold red] The '--web-apply' flag requires the 'keyboard' module.")
        console.print("Please install it using: [cyan]pip install keyboard[/cyan]")
        sys.exit(1)

    if args.paths and len(args.paths) == 1 and args.paths[0].lower().endswith('.zip'):
        zip_path_to_cleanup = os.path.abspath(args.paths[0])
        temp_dir = tempfile.mkdtemp(prefix="combineCopy_zip_")
        atexit.register(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        
        console.print(f"[bold cyan]Extracting {args.paths[0]} to temporary directory...[/bold cyan]")
        try:
            with zipfile.ZipFile(args.paths[0], 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            extracted_items = os.listdir(temp_dir)
            if len(extracted_items) == 1 and os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
                root_dir = os.path.join(temp_dir, extracted_items[0])
            else:
                root_dir = temp_dir
            args.paths = []
        except Exception as e:
            console.print(f"[bold red]Failed to extract zip file: {e}[/bold red]")
            sys.exit(1)

    all_known_files = []

    if (args.auto or args.revert or args.orchestrate) and not (args.select or args.file_types or args.paths or args.system is not None or args.cli):
        if args.orchestrate:
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file, cli_mode=args.cli, xml_mode=args.xml)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
            return
        else:
            app = AutoAgentApp(root_dir, revert_mode=args.revert, web_mode=args.web, tfs_mode=args.tfs, xml_mode=args.xml, consult_mode=args.consult)
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
        from combinecopy.web.server import start_server
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
        missing_files_warnings = []

        if args.json_select:
            console.print("[bold cyan]Phase: Selection Parsing[/bold cyan]")
            import pyperclip
            from combinecopy.utils import extract_json_from_text, intelligent_json_fix, extract_xml_from_text, parse_xml_to_dict
            try:
                clipboard_content = pyperclip.paste().strip()
            except Exception as e:
                console.print(f"[bold red]Error reading clipboard:[/bold red] {e}")
                return
            
            if not clipboard_content:
                console.print("[bold red]Clipboard is empty.[/bold red]")
                return

            selection_data = None
            
            # First check for XML
            xml_results = extract_xml_from_text(clipboard_content)
            for xml_str in xml_results:
                data = parse_xml_to_dict(xml_str)
                if data and (data.get("phase") == "SELECT" or "files" in data or "functions" in data):
                    selection_data = data
                    break
                    
            # Fallback to JSON
            if not selection_data:
                results = extract_json_from_text(clipboard_content)
                for json_str in results:
                    data, _ = intelligent_json_fix(json_str)
                    if data and isinstance(data, dict):
                        if data.get("phase") == "SELECT" or "files" in data or "functions" in data:
                            selection_data = data
                            break

            if not selection_data:
                console.print("[bold red]No valid SELECT JSON or XML payload found on clipboard.[/bold red]")
                return

            console.print("[green]Found valid JSON selection payload.[/green]")
            found_files = []
            important_files = []
            partial_files = {}
            
            from combinecopy.utils import prime_ast_cache, get_cached_blocks, resolve_paths

            with console.status("[bold green]Scanning directory structure...[/bold green]", spinner="dots"):
                scanned_files = get_files_recursive(root_dir, 0, max_depth, ext_filters, exclude_dirs=args.exclude)
                
            prime_ast_cache(root_dir, scanned_files)

            full_files_list = selection_data.get("files", [])
            functions_list = selection_data.get("functions", [])
            
            req_paths = set(full_files_list)
            for entry in functions_list:
                if entry.get("path"):
                    req_paths.add(entry.get("path"))
                    
            resolved_map, ambiguous_map, missing_list = resolve_paths(req_paths, scanned_files, root_dir)
            
            if ambiguous_map or missing_list:
                from combinecopy.tui.resolve import ResolutionApp
                app = ResolutionApp(ambiguous_map, missing_list)
                user_resolved = app.run()
                if user_resolved is None:
                    console.print("[bold yellow]Resolution cancelled by user.[/bold yellow]")
                    return
                resolved_map.update(user_resolved)
                
            missing_files_warnings = [p for p in req_paths if p not in resolved_map]
            if missing_files_warnings:
                console.print(f"[bold yellow]Warning: {len(missing_files_warnings)} requested files could not be resolved and will be skipped.[/bold yellow]")

            found_files_final = []
            for f in full_files_list:
                if f in resolved_map:
                    rel_path = resolved_map[f]
                    abs_path = os.path.abspath(os.path.join(root_dir, rel_path))
                    found_files_final.append(abs_path)
                    important_files.append(abs_path)
                    console.print(f"  Selected full file: [cyan]{rel_path}[/cyan] (Resolved from {f})")

            missing_funcs_to_search = []
            for entry in functions_list:
                fpath = entry.get("path")
                names = entry.get("names", [])
                
                if fpath in resolved_map:
                    rel_path = resolved_map[fpath]
                    abs_path = os.path.abspath(os.path.join(root_dir, rel_path))
                    
                    blocks = get_cached_blocks(abs_path, root_dir)
                    found_names = []
                    for name in names:
                        found_block = False
                        for b in blocks:
                            if name in b["name"]:
                                if abs_path not in partial_files:
                                    partial_files[abs_path] = []
                                # Avoid appending duplicates if signatures overlap
                                if b not in partial_files[abs_path]:
                                    partial_files[abs_path].append(b)
                                found_block = True
                        if found_block:
                            found_names.append(name)
                        else:
                            missing_funcs_to_search.append(name)
                    
                    if found_names:
                        if abs_path not in found_files_final:
                            found_files_final.append(abs_path)
                        console.print(f"  Selected functions from [cyan]{rel_path}[/cyan]: {', '.join(found_names)}")
                else:
                    # File wasn't resolved, queue all its requested functions for a workspace search
                    missing_funcs_to_search.extend(names)

            # De-duplicate missing function list
            missing_funcs_to_search = list(dict.fromkeys(missing_funcs_to_search))
            
            if missing_funcs_to_search:
                from combinecopy.utils import search_ast_for_functions, get_blocks_by_name
                candidate_map = search_ast_for_functions(missing_funcs_to_search, root_dir)
                
                ambiguous_funcs = {k: v for k, v in candidate_map.items() if v}
                unfound_funcs = [k for k in missing_funcs_to_search if not candidate_map.get(k)]
                
                if ambiguous_funcs:
                    from combinecopy.tui.resolve import FunctionResolutionApp
                    app = FunctionResolutionApp(ambiguous_funcs)
                    func_resolutions = app.run()
                    if func_resolutions is None:
                        console.print("[bold yellow]Function resolution cancelled by user.[/bold yellow]")
                        return
                        
                    for fname, selected_paths in func_resolutions.items():
                        if not selected_paths:
                            console.print(f"  [yellow]Skipped[/yellow] Function/Class '[red]{fname}[/red]'")
                            continue
                        for spath in selected_paths:
                            abs_p = os.path.abspath(os.path.join(root_dir, spath))
                            blocks = get_blocks_by_name(abs_p, root_dir, fname)
                            if blocks:
                                if abs_p not in found_files_final:
                                    found_files_final.append(abs_p)
                                if abs_p not in partial_files:
                                    partial_files[abs_p] = []
                                existing_names = [b["name"] for b in partial_files[abs_p]]
                                for b in blocks:
                                    if b["name"] not in existing_names:
                                        partial_files[abs_p].append(b)
                                        console.print(f"  Resolved and selected function [cyan]{b['name']}[/cyan] in [cyan]{spath}[/cyan]")

                for uf in unfound_funcs:
                    console.print(f"  [yellow]Warning:[/yellow] Function/Class '[red]{uf}[/red]' could not be found anywhere in the workspace.")

            if not found_files_final:
                console.print("[bold red]No files were successfully selected from the JSON payload.[/bold red]")
                return

            found_files = found_files_final
            all_known_files = list(found_files)

        else:
            if args.paths:
                found_files = []
                important_files = []
                
                # Single file special casing for root_dir adjustment
                if len(args.paths) == 1 and os.path.isfile(args.paths[0]):
                    target_path = os.path.abspath(args.paths[0])
                    if not target_path.startswith(root_dir):
                        root_dir = os.path.dirname(target_path)
                
                for p in args.paths:
                    target_path = os.path.abspath(p)
                    if os.path.isfile(target_path):
                        found_files.append(target_path)
                        important_files.append(target_path)
                        console.print(f"[green]Targeting file:[/green] {p}")
                    elif os.path.isdir(target_path):
                        with console.status(f"[bold green]Scanning directory: {p}...[/bold green]", spinner="dots"):
                            dir_files = get_files_recursive(target_path, 0, max_depth, ext_filters, exclude_dirs=args.exclude)
                            found_files.extend(dir_files)
                    else:
                        console.print(Panel(f"Path not found: {p}", title="Error", style="bold red"))
                        return
                        
                # Deduplicate while preserving order
                found_files = list(dict.fromkeys(found_files))
                important_files = list(dict.fromkeys(important_files))
            else:
                with console.status("[bold green]Scanning directory structure...[/bold green]", spinner="dots"):
                    found_files = get_files_recursive(root_dir, 0, max_depth, ext_filters, exclude_dirs=args.exclude)
            
            if args.file_culling or args.select:
                from combinecopy.utils import prime_ast_cache
                prime_ast_cache(root_dir, found_files)
                
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

        only_files_targeted = bool(args.paths) and all(os.path.isfile(p) for p in args.paths)
        is_targeted = args.select or only_files_targeted
        if total_files > 250 and not is_targeted:
            app = ConfirmCopyApp(total_files)
            confirmed = app.run()
            if not confirmed:
                console.print(Panel("Large copy operation cancelled.", title="Cancelled", style="bold yellow"))
                return
    
        display_summary(root_dir, max_depth, ext_filters, batch_count, total_files)
    
        agent_type = "orchestrator" if args.orchestrate else "cli" if args.cli else "default"

        user_request_data = None
        if args.system is not None or args.cli:
            console.print("[bold cyan]Phase: Instruction & System Prompt[/bold cyan]")
            sys_arg = args.system if args.system else 'DEFAULT'
            if sys_arg == 'DEFAULT' or sys_arg == '':
                sys_prompt_text = get_system_prompt(agent_type=agent_type, file_cull=args.file_culling, xml_mode=args.xml, consult=args.consult)
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

        files_per_batch = math.ceil(total_files / batch_count)
        console.print(f"\n[dim]Splitting into {batch_count} batch(es). ~{files_per_batch} files/batch.[/dim]\n")
    
        for i in range(batch_count):
            batch_num = i + 1
            start_index = i * files_per_batch
            end_index = start_index + files_per_batch
            current_batch_files = found_files[start_index:end_index]
            
            if not current_batch_files:
                break
    
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
                    file_context_buffer = []
                    
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
                            file_context_buffer.append(separator)
                            file_context_buffer.append(f"FILE: {rel_path}")
                            file_context_buffer.append(separator)
                            file_context_buffer.append(f"```{lang}")
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
                                    file_context_buffer.append("".join(partial_content))
                                else:
                                    file_context_buffer.append(content)
                            except Exception as e:
                                progress.console.print(f"  [red]![/red] Error reading {rel_path}: {e}")
                                file_context_buffer.append(f"[Error reading file: {e}]")
                            file_context_buffer.append("```")
                            file_context_buffer.append("\n")
                        else:
                            rel_path = os.path.relpath(file_path, root_dir)
                            progress.console.print(f"  [cyan]ℹ[/cyan] Included [bold]{rel_path}[/bold] in AST Map")
                        progress.advance(task)
                    if stop_event.is_set(): return
                    
                    if missing_files_warnings:
                        file_context_buffer.append("\n--- SYSTEM NOTE: MISSING FILES ---")
                        file_context_buffer.append("The following files were requested but could not be found or resolved in the workspace:")
                        for mfw in missing_files_warnings:
                            file_context_buffer.append(f"- {mfw}")
                        file_context_buffer.append("Please check your paths and request them again if necessary.\n")
                        
                    file_context_str = "\n".join(file_context_buffer)
                    full_text = ""
                    
                    if batch_count == 1:
                        if user_request_data:
                            full_text = build_prompt(
                                user_request=user_request_data["request"],
                                file_context=file_context_str,
                                ast_map=generate_tree_string(found_files, root_dir) if args.file_culling else "",
                                file_cull=args.file_culling,
                                system_prompt=user_request_data["system"],
                                agent_type=agent_type,
                                xml_mode=args.xml,
                                consult=args.consult
                            )
                        else:
                            full_text = file_context_str
                    else:
                        parts = []
                        if batch_num == 1 and user_request_data:
                            parts.append(get_user_prompt(user_request_data["request"]))
                            if args.file_culling:
                                parts.append(get_ast(generate_tree_string(found_files, root_dir)))
                            parts.append(get_file_context(file_context_str))
                            parts.append(get_user_prompt(user_request_data["request"]))
                            parts.append(f"--- SYSTEM INSTRUCTIONS ---\n{user_request_data['system']}")
                        else:
                            parts.append(file_context_str)
                            
                        if batch_num == batch_count and user_request_data:
                            parts.append(get_user_prompt(user_request_data["request"], reminder=True))
                            parts.append(get_system_prompt_important(agent_type, xml_mode=args.xml))
                            
                        full_text = "\n\n".join(parts)

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
            app = OrchestratorAgentApp(root_dir, use_file_clipboard=args.file, cli_mode=args.cli, xml_mode=args.xml)
            result = app.run()
            if result:
                console.print(Panel("Orchestrator payload successfully copied to clipboard.", title="Success", style="bold green"))
        else:
            phase_name = "Auto Agent Execution (Revert Mode)" if args.revert else "Auto Agent Execution"
            if args.web_apply:
                phase_name += " [WEB MACRO MODE]"
            console.print(f"\n[bold cyan]Phase: {phase_name}[/bold cyan]")
            app = AutoAgentApp(root_dir, all_known_files, revert_mode=args.revert, ignore_initial_clipboard=True, web_mode=args.web_apply, tfs_mode=args.tfs, xml_mode=args.xml, consult_mode=args.consult)
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
