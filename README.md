# AI-Tools
A repo of tools that interact with AI and local repositories.

## Overview of Tools

This repository contains two primary Python-based utility tools designed to simplify AI-assisted development and remote deployments:
1. **`combineCopy.py`**: A workspace context assembler and automated AI execution agent.
2. **`ftpapp.py`**: A Git-integrated FTP deployment tool to sync changes to a remote server.

---

## 1. combineCopy.py (AI Workspace & Execution Agent)

`combineCopy.py` is a workflow tool that bridges local source code files with large language models (LLMs). It handles the full pair-programming lifecycle: scanning files, selecting context, sending prompt payloads, and automatically applying the AI's requested edits back to the local workspace.

### Key Features
- **Context Assembly**: Recursively scans directories to collect source files, filtering by file extensions and ignoring standard dependency folders (such as `.git`, `node_modules`, `.venv`, etc.).
- **Interactive TUI File Selector**: Launches a terminal user interface (TUI) allowing developers to select or deselect specific files, perform search queries, and view selected file counts before packaging. Includes AST mapping to cull unnecessary file context.
- **Local Kanban Board**: Built-in TUI Kanban board to track tasks, attach specific context files, and dispatch them to the AI continuously.
- **Continuous AI Execution Agent**: Monitors the system clipboard for formatted JSON instruction payloads returned from an AI agent. It can execute local workspace changes:
  - **Create**: Add complete, functional new files.
  - **Modify**: Update files using exact/fuzzy search-and-replace blocks, or regex-based replacement rules.
  - **Delete**: Safely remove specified files.
- **Prompt Packaging & Batching**: Automatically structures context with system instructions and user requests. Can partition large datasets into smaller clipboard batches.
- **Orchestration Mode**: Allows an orchestrator-level model to design a precise execution plan and prompt for downstream models.
- **Web Macro Mode**: Translates applies into keyboard stroke macros for browser-based development environments.

### Common Options
- `-l, --limit`: Set maximum recursion depth for scanning directories.
- `-f, --file_types`: List of space-separated extensions to include (e.g., `-f py js html`).
- `-e, --exclude`: List of space-separated directory names to exclude.
- `-s, --select`: Open the interactive TUI selector to pick files.
- `-a, --auto`: Run in continuous clipboard-monitoring execution mode.
- `-r, --revert`: Run in continuous AI listener mode but reverse all changes.
- `-o, --orchestrate`: Run in orchestrator planning mode to produce execution prompts.
- `-k, --kanban`: Launch the persistent Kanban board interface.
- `--file-culling`: Enable file culling / AST selection mode.
- `-b, --batches`: Configure batch counts for copying large workspace contexts.
- `--web`: Enable keyboard macro emulation mode for web IDE targets.

---

## 2. ftpapp.py (Git-to-FTP Sync & Deployment Tool)

`ftpapp.py` is a deployment utility that automates the transfer of modified code to a remote hosting environment over FTP. Instead of manually copying files, it identifies exactly what changed by reading local Git history.

### Key Features
- **Git Integration**: Analyzes modified, added, and deleted statuses between a baseline commit (or offset such as `HEAD~1`) and the current repository `HEAD`.
- **Interactive TUI Setup**: Prompts for FTP host details, credentials, and presents a navigable list of recent Git commits to define the deployment delta.
- **Background Transfer Engine**: Runs network transfers on a separate background thread to maintain UI responsiveness.
- **Real-Time Progress & Logs**: Displays individual file queue progress, network speed, total transferred size, and the live FTP protocol log dialogue stream.
- **Automatic Remote Directories**: Detects and creates nested directory paths on the target FTP server dynamically during upload.

### Common Options
- `-f, --ftp`: Target FTP connection host and port (e.g., `192.168.1.1:21`).
- `-u, --username`: FTP credentials login user.
- `-p, --password`: FTP credentials login password.
- `-c, --commit`: Commit hash or revision baseline to run diff against.
- `-r, --repo-loc`: Target root directory on the remote server (e.g., `/httpdocs/`).
