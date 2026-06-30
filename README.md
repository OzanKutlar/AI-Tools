# AI-Tools

A repository of terminal user interface (TUI) utility tools that interact with AI models and local workspaces to assist with software development workflows.

## Installation

This repository is configured as a Python package. You can install it locally to make the tools available globally on your command line.

```bash
# Clone the repository
git clone https://github.com/OzanKutlar/AI-Tools
cd AI-Tools

# Install the tools globally
pip install .

# Or install in editable mode for active development
pip install -e .
```

Once installed, your system will have access to the following terminal commands: `combineCopy`, `ftpapp`, `webapp`, and `app` (which is a shortcut that automatically runs `combineCopy -a`).

## Overview of Tools

This project consists of three primary Python-based utilities designed for AI-assisted development and remote deployments:
1. **`combineCopy`**: A workspace context assembler, file selector, and automated clipboard-based AI execution agent.
2. **`ftpapp`**: A Git-integrated FTP deployment tool to synchronize workspace changes to a remote hosting environment.
3. **`webapp`**: A Git-integrated keyboard-emulation apply tool to automate transferring file changes to browser-based editors/IDEs.

---

## 1. combineCopy (AI Workspace & Execution Agent)

`combineCopy` is a terminal user interface and background worker utility that helps assemble local source code files for LLM prompt context, and executes code updates (file creations, search-and-replace modifications, deletions, and CLI commands) received back from the AI.

### Key Workflows and Features
- **Context Assembly**: Recursively scans folders to collect source files, filtering by file extensions and respecting configured exclusion lists. You can target specific files, directories, or even pass a `.zip` archive which will be natively extracted and scanned on the fly.
- **Interactive File Selector TUI**: Allows users to interactively pick files to include and toggle "full context" versus "AST map only" modes.
- **Continuous AI Execution Agent**: Listens to the system clipboard for structured JSON (or XML) instructions representing file operations (Create, Modify, Delete, Command) and applies them locally with diff previews.
- **Orchestration & Consultation**: Enables an orchestrator-level model to plan execution steps for downstream coding models, and allows agents to "consult" external air-gapped LLMs for complex logic.
- **Multiple Environment Support**: Can interface natively with Git, TFVC/TFS (`--tfs`), or Web IDEs (`--web-apply`).
- **Web UI Server**: Alternatively launch a local Flask-based browser UI for interacting with your workspace context.

### Common Usage Examples

```bash
# Standard prompt generation filtered by file type
combineCopy -f py
combineCopy -f cs kt xml

# Target specific files directly and inject the system prompt
combineCopy .\combineCopy.py --system
combineCopy .\architecture.svg --system
combineCopy ".\Creative writing\Untitled Book\"

# Native ZIP file support (extracts and filters automatically)
combineCopy .\FENS401_402_2026_FS2.zip -f tex bib

# File selector TUI + Auto-Listener + System Prompt (Common refactoring combo)
combineCopy -f cs -sa --system

# File selector + AST Culling
combineCopy --file-cull -s

# Complex Mobile App / Multi-language build with Agent Listener
combineCopy -f gradle kt xml -s -a --system
```

### Complete List of Arguments

**Path Targets**
- `paths`: Specific files, directories, or `.zip` archives to include (bypasses full directory scan).

**Filtering & Output Control**
- `-l, --limit <int>`: Max recursion depth for scanning directories (default: 100).
- `-f, --file_types <ext...>`: Space-separated file extensions to include (e.g., `-f py js html`).
- `-e, --exclude <dir...>`: Space-separated directory names to exclude from the scan.
- `-b, --batches <int>`: Number of batches to split large workspace context copies into (default: 1).
- `--file-culling, --file-cull`: Enable file culling / AST (Abstract Syntax Tree) map generation mode.

**Interactive & UI Modes**
- `-s, --select`: Open the interactive TUI selector to manually pick files.
- `--system`: Open a TUI to inject the system prompt and type your user instructions. Optionally provide a path to a custom system prompt file (e.g. `--system custom.txt`).
- `--web`: Launch the local Web UI server on `127.0.0.1:5000`.

**AI Agent & Execution Modes**
- `-a, --auto`: Run in continuous AI listener mode (monitors clipboard for JSON/XML execution payloads).
- `-r, --revert`: Run in continuous AI listener mode, but reverse all modifications.
- `-o, --orchestrate`: Run in orchestrator mode to generate a precise execution plan and prompt.
- `--cli`: Enable CLI Mode. Modifies the system prompt to allow the AI to output terminal commands.
- `--consult`: Enable the CONSULT phase, allowing the AI to pause and ask abstract questions to an external Expert LLM.
- `-x, --xml`: Instruct the AI to use XML tags for payloads instead of JSON to completely avoid quote escaping issues.
- `-js, --json-select`: Parse a JSON/XML selection payload directly from the clipboard to automatically select files/functions (used during the EXPLORATION phase).

**Environment Integrations**
- `--web-apply`: Enable web macro mode. Translates AI execution applies into simulated keyboard strokes (`keyboard` module required) for Web IDEs.
- `--tfs`: Use TFVC (`tf.exe`) instead of `git` for checkout, add, delete, and checkin operations.

**Clipboard & File Outputs**
- `--file`: Save the generated prompt to a temporary `.txt` file and copy the *file itself* to the clipboard (useful for attaching to web chats).
- `--system-only`: Copy only the raw system prompt text to the clipboard and exit.

---

## 2. ftpapp (Git-to-FTP Sync & Deployment Tool)

`ftpapp` is a deployment utility that automates the transfer of modified code to a remote hosting environment over FTP. Instead of manually copying files, it identifies exactly what changed by reading local Git history.

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
- `--force`: Force changes by skipping remote state verification.

---

## 3. webapp (Git-to-Web Apply & Keyboard Macro Emulation)

`webapp` is an apply utility designed to automate uploading workspace updates directly into browser-based text editors or IDEs. Instead of copy-pasting code by hand, it uses keyboard macros to select all text and overwrite the contents with your local changes.

### Key Features
- **Git-Integrated Delta Detection**: Identifies modifications, additions, and deletions relative to a selected baseline commit hash or git reference.
- **Keyboard-Emulated Apply Sequence**: Uses system-level hooks via the `keyboard` module to capture a global hotkey (e.g., `+`). When triggered, it copies the active file's code to the clipboard, clears the active input (`Ctrl+A`), and pastes (`Ctrl+V`).
- **Auto-Advancing Status Queue**: Tracks active file transfers and progresses to the next file sequentially inside the interactive TUI environment.
- **Manual Action Override**: Offers keyboard options inside the TUI to skip (`s`), force-complete (`f`), or go back (`p`) to the previous item in the queue.

### Common Options
- `-c, --commit`: Git commit hash or reference to diff against.
- `-k, --hotkey`: Custom trigger sequence (defaults to `+` / Numpad Plus).
