import os
import re
import difflib
import subprocess
from flask import Flask, request, jsonify, render_template_string

from cc_utils import get_files_recursive, safe_read_file, intelligent_json_fix, generate_tree_string
from cc_prompts import DEFAULT_SYSTEM_PROMPT_TEMPLATE

app = Flask(__name__)

# Globals configured at startup
ROOT_DIR = ""
MAX_DEPTH = 100
EXTENSIONS = None
EXCLUDE_DIRS = None

def compute_new_text(file_obj, old_text):
    if "content" in file_obj:
        return file_obj["content"]
    new_text = old_text
    for block in file_obj.get("search_replace", []):
        search = block.get("search", "")
        replace = block.get("replace", "")
        if search and search in new_text:
            new_text = new_text.replace(search, replace, 1)
        else:
            # Basic fallback fuzzy match by stripping lines
            search_norm = "\n".join(l.strip() for l in search.strip().split('\n') if l.strip())
            source_lines = new_text.split('\n')
            search_lines = search.strip('\n').split('\n')
            found = False
            for i in range(len(source_lines) - len(search_lines) + 1):
                window = '\n'.join(source_lines[i : i + len(search_lines)])
                window_norm = "\n".join(l.strip() for l in window.strip().split('\n') if l.strip())
                if window_norm == search_norm:
                    new_text = new_text.replace(window, replace, 1)
                    found = True
                    break
    for block in file_obj.get("regex_replace", []):
        pattern = block.get("pattern", "")
        replacement = block.get("replacement", "")
        if pattern:
            try:
                new_text = re.sub(pattern, replacement, new_text)
            except re.error:
                pass
    return new_text

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>CombineCopy Web UI</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .tab-active { border-bottom: 2px solid #d08c60; color: #d08c60; }
        pre { white-space: pre-wrap; }
    </style>
</head>
<body class="bg-[#2d2825] text-[#ead6c9] h-screen flex flex-col font-sans">
    <!-- Header -->
    <header class="bg-[#d08c60] text-[#2d2825] p-4 font-bold text-xl flex justify-between items-center">
        <span>CombineCopy Web Agent</span>
        <div class="space-x-6 text-sm">
            <button id="tab-context" class="tab-active px-2 py-1 uppercase tracking-wider" onclick="switchTab('context')">Context Builder</button>
            <button id="tab-apply" class="px-2 py-1 text-gray-800 uppercase tracking-wider" onclick="switchTab('apply')">Payload Applier</button>
        </div>
    </header>

    <!-- Context Builder -->
    <main id="view-context" class="flex-1 flex overflow-hidden">
        <aside class="w-1/3 bg-[#241f1c] border-r border-[#5a4d45] flex flex-col">
            <div class="p-3 bg-[#4a3f39] text-[#d08c60] font-bold text-center border-b border-[#5a4d45]">Workspace Files</div>
            <div class="p-4 flex-1 overflow-y-auto text-sm" id="file-list">
                <div class="text-gray-500 italic">Scanning files...</div>
            </div>
        </aside>
        <section class="flex-1 p-6 flex flex-col">
            <label class="font-bold mb-2 text-[#d08c60]">User Request / Goal:</label>
            <textarea id="user-request" class="w-full h-32 bg-[#1e1a18] border border-[#5a4d45] p-3 mb-4 focus:outline-none focus:border-[#d08c60] text-gray-300 font-mono text-sm rounded" placeholder="Describe the task or issue for the AI..."></textarea>

            <button onclick="generatePrompt()" class="bg-green-700 hover:bg-green-600 text-white font-bold py-2 px-6 rounded shadow mb-6 w-48 transition-colors">Generate Prompt</button>

            <label class="font-bold mb-2 text-[#d08c60]">Generated Prompt Payload:</label>
            <textarea id="prompt-output" class="w-full flex-1 bg-[#1e1a18] border border-[#5a4d45] p-3 mb-4 text-xs font-mono text-gray-300 rounded" readonly></textarea>
            <button onclick="copyPrompt()" class="bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 px-6 rounded shadow w-56 transition-colors">Copy to Clipboard</button>
        </section>
    </main>

    <!-- Payload Applier -->
    <main id="view-apply" class="flex-1 flex flex-col p-6 hidden overflow-hidden">
        <label class="font-bold mb-2 text-[#d08c60]">Paste LLM Execution JSON:</label>
        <textarea id="payload-input" class="w-full h-48 bg-[#1e1a18] border border-[#5a4d45] p-3 mb-4 focus:outline-none focus:border-[#d08c60] text-gray-300 font-mono text-sm rounded"></textarea>
        <button onclick="previewPayload()" class="bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 px-6 rounded shadow mb-6 w-48 transition-colors">Preview Changes</button>

        <div class="flex-1 overflow-y-auto border border-[#5a4d45] bg-[#241f1c] p-4 rounded" id="diff-container">
            <div class="text-gray-500 italic text-center mt-10">Paste a payload above and click Preview to view diffs.</div>
        </div>
    </main>

    <script>
        function switchTab(tab) {
            document.getElementById('view-context').classList.toggle('hidden', tab !== 'context');
            document.getElementById('view-apply').classList.toggle('hidden', tab !== 'apply');
            document.getElementById('tab-context').classList.toggle('tab-active', tab === 'context');
            document.getElementById('tab-context').classList.toggle('text-gray-800', tab !== 'context');
            document.getElementById('tab-apply').classList.toggle('tab-active', tab === 'apply');
            document.getElementById('tab-apply').classList.toggle('text-gray-800', tab !== 'apply');
        }

        async function loadFiles() {
            try {
                const res = await fetch('/api/scan');
                const data = await res.json();
                const list = document.getElementById('file-list');
                list.innerHTML = data.files.map(f => `
                    <label class="flex items-center space-x-3 mb-2 cursor-pointer hover:bg-[#3c3431] p-1 rounded transition-colors">
                        <input type="checkbox" value="${f}" class="file-cb accent-[#d08c60] w-4 h-4">
                        <span class="truncate" title="${f}">${f}</span>
                    </label>
                `).join('');
            } catch (e) {
                document.getElementById('file-list').innerHTML = `<div class="text-red-500">Error loading files. Is the server running?</div>`;
            }
        }

        async function generatePrompt() {
            const cbs = Array.from(document.querySelectorAll('.file-cb:checked')).map(cb => cb.value);
            const req = document.getElementById('user-request').value;
            const btn = event.target;
            btn.innerText = "Generating...";
            
            try {
                const res = await fetch('/api/generate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({files: cbs, request: req})
                });
                const data = await res.json();
                document.getElementById('prompt-output').value = data.prompt;
            } finally {
                btn.innerText = "Generate Prompt";
            }
        }

        function copyPrompt() {
            const text = document.getElementById('prompt-output').value;
            navigator.clipboard.writeText(text).then(() => {
                const btn = event.target;
                const old = btn.innerText;
                btn.innerText = "Copied!";
                setTimeout(() => btn.innerText = old, 2000);
            });
        }

        let currentFiles = [];

        async function previewPayload() {
            const payload = document.getElementById('payload-input').value;
            const container = document.getElementById('diff-container');
            container.innerHTML = `<div class="text-gray-400 italic">Validating and computing diffs...</div>`;
            
            try {
                const res = await fetch('/api/preview', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({payload})
                });
                const data = await res.json();
                if (data.error) {
                    container.innerHTML = `<div class="text-red-500 font-bold p-4 bg-[#3c3431] rounded border border-red-500">Error parsing payload:<br><br>${escapeHtml(data.error)}</div>`;
                    return;
                }
                currentFiles = data.files;
                renderDiffs();
            } catch (e) {
                container.innerHTML = `<div class="text-red-500">Network error communicating with the local server.</div>`;
            }
        }

        function renderDiffs() {
            const container = document.getElementById('diff-container');
            if (currentFiles.length === 0) {
                container.innerHTML = `<div class="text-gray-500">No files found in payload.</div>`;
                return;
            }
            
            container.innerHTML = `<button onclick="applyAll()" class="bg-green-700 hover:bg-green-600 text-white font-bold py-2 px-6 rounded shadow mb-6">Apply All Pending Files</button><br>` + currentFiles.map((f, i) => {
                const colorMap = { "modify": "#eab308", "create": "#22c55e", "delete": "#ef4444", "command": "#a855f7" };
                const color = colorMap[f.action] || "#d08c60";
                return `
                <div class="mb-6 border border-[#5a4d45] rounded bg-[#1e1a18] shadow">
                    <div class="flex justify-between items-center p-3 bg-[#2d2825] border-b border-[#5a4d45] rounded-t">
                        <h3 class="font-bold tracking-wide" style="color: ${color}">[${f.action.toUpperCase()}] ${f.path}</h3>
                        <button onclick="applyFile(${i})" id="btn-apply-${i}" class="bg-[#d08c60] hover:bg-[#b0734c] text-[#2d2825] font-bold py-1 px-4 rounded transition-colors">Apply File</button>
                    </div>
                    <pre class="text-xs font-mono text-gray-300 p-4 overflow-x-auto">${formatDiff(f.diff)}</pre>
                </div>
            `}).join('');
        }

        function formatDiff(diffText) {
            return escapeHtml(diffText).split('\n').map(line => {
                if (line.startsWith('+') && !line.startsWith('+++')) return `<span class="text-green-400">${line}</span>`;
                if (line.startsWith('-') && !line.startsWith('---')) return `<span class="text-red-400">${line}</span>`;
                if (line.startsWith('@@')) return `<span class="text-blue-400">${line}</span>`;
                return line;
            }).join('\n');
        }

        async function applyFile(index) {
            const fileObj = currentFiles[index];
            const btn = document.getElementById(`btn-apply-${index}`);
            const oldText = btn.innerText;
            btn.innerText = "Applying...";
            
            try {
                const res = await fetch('/api/apply', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(fileObj)
                });
                const data = await res.json();
                if (data.success) {
                    btn.innerText = "Applied ✓";
                    btn.classList.replace('bg-[#d08c60]', 'bg-green-600');
                    btn.disabled = true;
                } else {
                    alert('Error applying: ' + data.error);
                    btn.innerText = oldText;
                }
            } catch (e) {
                alert('Network error.');
                btn.innerText = oldText;
            }
        }

        async function applyAll() {
            for (let i = 0; i < currentFiles.length; i++) {
                const btn = document.getElementById(`btn-apply-${i}`);
                if (!btn.disabled) {
                    await applyFile(i);
                }
            }
            alert('Finished applying files!');
        }

        function escapeHtml(unsafe) {
            return unsafe
                 .replace(/&/g, "&amp;")
                 .replace(/</g, "&lt;")
                 .replace(/>/g, "&gt;")
                 .replace(/"/g, "&quot;")
                 .replace(/'/g, "&#039;");
        }

        // Init
        loadFiles();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/scan')
def scan():
    files = get_files_recursive(ROOT_DIR, 0, MAX_DEPTH, EXTENSIONS, EXCLUDE_DIRS)
    rel_files = [os.path.relpath(f, ROOT_DIR) for f in files]
    return jsonify({"files": rel_files})

@app.route('/api/generate', methods=['POST'])
def generate():
    req_data = request.json
    paths = req_data.get("files", [])
    user_req = req_data.get("request", "")
    
    buffer = []
    if user_req:
        buffer.append("--- USER REQUEST ---")
        buffer.append(user_req)
        buffer.append("\n--- SYSTEM INSTRUCTIONS ---")
        buffer.append(DEFAULT_SYSTEM_PROMPT_TEMPLATE.replace('{FILE_CULLING_INSTRUCTION}\n', '').replace('{FILE_CULLING_INSTRUCTION}', ''))
        buffer.append("\n--- USER REQUEST ---")
        buffer.append(user_req)

    all_files = get_files_recursive(ROOT_DIR, 0, MAX_DEPTH, EXTENSIONS, EXCLUDE_DIRS)
    buffer.append("\n--- DIRECTORY AST MAP ---")
    buffer.append(generate_tree_string(all_files, ROOT_DIR))

    buffer.append("\n--- FILE CONTEXT ---")
    sep = "-" * 35
    for p in paths:
        full_path = os.path.join(ROOT_DIR, p)
        buffer.append(sep)
        buffer.append(f"FILE: {p}")
        buffer.append(sep)
        _, ext = os.path.splitext(p)
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

    if user_req:
        buffer.append("--- USER REQUEST (Reminder) ---")
        buffer.append(user_req)

    return jsonify({"prompt": "\n".join(buffer)})

@app.route('/api/preview', methods=['POST'])
def preview():
    payload_str = request.json.get("payload", "")
    data, error_str = intelligent_json_fix(payload_str)
    
    if not data or "files" not in data:
        # Try to provide a helpful parsing error via standard json if intelligent_fix fully failed
        import json
        try:
            json.loads(payload_str)
            return jsonify({"error": "JSON parsed but 'files' key is missing or schema is incorrect."}), 400
        except json.JSONDecodeError as e:
            return jsonify({"error": f"Invalid JSON syntax: {e.msg} at line {e.lineno} col {e.colno}"}), 400

    results = []
    for file_obj in data["files"]:
        action = file_obj.get("action", "modify").lower()
        if action == "command":
            results.append({
                "path": file_obj.get("command", "Unknown Command"),
                "action": action,
                "diff": "This is a CLI Command. Clicking Apply will execute it in the terminal.",
                "file_obj": file_obj
            })
            continue

        path = file_obj.get("path", "")
        full_path = os.path.join(ROOT_DIR, path)
        old_text = ""
        if os.path.exists(full_path):
            try:
                old_text = safe_read_file(full_path)
            except:
                old_text = "[Error reading existing file]"

        if action == "delete":
            new_text = ""
        else:
            new_text = compute_new_text(file_obj, old_text)

        diff = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
            n=3
        ))
        diff_text = "".join(diff) if diff else "No changes detected."

        results.append({
            "path": path,
            "action": action,
            "diff": diff_text,
            "new_text": new_text,
            "file_obj": file_obj
        })

    return jsonify({"files": results})

@app.route('/api/apply', methods=['POST'])
def apply_file():
    req = request.json
    action = req.get("action", "modify").lower()
    
    if action == "command":
        file_obj = req.get("file_obj", {})
        cmd = file_obj.get("command", "")
        if not cmd:
            return jsonify({"error": "Missing command."}), 400
        try:
            subprocess.run(cmd, shell=True, cwd=ROOT_DIR, check=True)
            return jsonify({"success": True})
        except subprocess.CalledProcessError as e:
            return jsonify({"error": f"Command failed with exit code {e.returncode}"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    path = req.get("path")
    new_text = req.get("new_text", "")
    full_path = os.path.join(ROOT_DIR, path)

    try:
        if action == "delete":
            if os.path.exists(full_path):
                os.remove(full_path)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def start_server(root_dir, max_depth, extensions, exclude_dirs):
    global ROOT_DIR, MAX_DEPTH, EXTENSIONS, EXCLUDE_DIRS
    ROOT_DIR = root_dir
    MAX_DEPTH = max_depth
    EXTENSIONS = extensions
    EXCLUDE_DIRS = exclude_dirs
    
    # Disable Flask startup text spam
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app.run(host='127.0.0.1', port=5000)
