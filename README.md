# AI-Tools

A repository of terminal user interface (TUI) utility tools that interact with AI models and local workspaces to assist with software development workflows.

## Overview of Tools

This project consists of two primary Python-based utilities designed for AI-assisted development and remote deployments:
1. **`combineCopy.py`**: A workspace context assembler, file selector, local Kanban board, and automated clipboard-based AI execution agent.
2. **`ftpapp.py`**: A Git-integrated FTP deployment tool to synchronize workspace changes to a remote hosting environment.

---

## 1. combineCopy.py (AI Workspace & Execution Agent)

`combineCopy.py` is a terminal user interface and background worker utility that helps assemble local source code files for LLM prompt context, and executes code updates (file creations, search-and-replace modifications, and deletions) received back from the AI.

### Key Workflows and Features
- **Context Assembly**: Recursively scans folders to collect source files, filtering by file extensions and respecting configured exclusion lists (such as `.git`, `node_modules`, `.venv`, etc.).
- **Interactive File Selector TUI**: Allows users to interactively pick files to include, toggle "full context" versus "AST map only" modes (with file culling), search files, and see a running count of selected documents.
- **Continuous AI Execution Agent**: Listens to the system clipboard for structured JSON instructions representing file operations (Create, Modify, Delete, and Terminal Command Execution) and applies them locally with diff previews.
- **Local Kanban Board TUI**: A persistent local task tracker stored in `.cc_kanban.json`. Users can create tasks, attach specific context files, generate model prompts, and dispatch them to the AI agent seamlessly. 
- **Post-Session Summaries**: Exiting the Kanban board prints a clean, stylized action log to the console summarizing all tasks created, recalled, completed, and files modified during the session.
- **Orchestration Mode**: Enables an orchestrator-level model to plan execution steps and package individual task contexts for downstream coding models.

### Common Options
- `-l, --limit`: Set maximum recursion depth for scanning directories.
- `-f, --file_types`: List of space-separated extensions to include (e.g., `-f py js html`).
- `-e, --exclude`: List of space-separated directory names to exclude.
- `-s, --select`: Open the interactive TUI selector to pick files.
- `-a, --auto`: Run in continuous AI listener mode (clipboard monitor).
- `-r, --revert`: Run in continuous AI listener mode but reverse all modifications.
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
