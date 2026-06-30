# AI-Tools

This repository provides a suite of command-line utilities designed to interface local development workspaces with Large Language Models (LLMs). The primary focus of the project is `combineCopy`, a comprehensive context assembly and automated execution agent. Secondary utilities are included to facilitate state-based deployments to restrictive environments.

## Installation

The repository is configured as a Python package. Installing it locally exposes the tools globally on your command line environment.

```bash
git clone https://github.com/OzanKutlar/AI-Tools
cd AI-Tools
pip install -e .
```

Once installed, the `combineCopy`, `ftpapp`, and `webapp` commands become available. The `app` command is also registered as an alias for `combineCopy -a`.

## combineCopy: Context Assembly and Execution Agent

The `combineCopy` utility is designed to eliminate the manual overhead of moving code between local IDEs and LLM interfaces. It operates across two primary pipelines: context aggregation and automated execution.

### Architecture and Methodology

**Context Assembly Pipeline:** To generate coherent and mechanically accurate responses, LLMs require precise workspace context. `combineCopy` utilizes a recursive scanning algorithm to aggregate source code based on user-defined file extensions and exclusion parameters. To optimize the context window and prevent token exhaustion, the system employs an Abstract Syntax Tree (AST) mapping system (file culling). This provides the LLM with a structural overview of the codebase without injecting full file contents unless explicitly requested. The pipeline also natively extracts and parses `.zip` archives on the fly. 

**Automated Execution Pipeline:** Traditional LLM interactions require manual copying and pasting of generated code, which is prone to human error. To address this, `combineCopy` implements a background execution agent that monitors the system clipboard for structured JSON or XML payloads. Upon detecting a valid payload, the engine parses the requested actions—such as file creations, targeted search-and-replace modifications, regex operations, and terminal command executions. These operations are validated against the current local state before being applied. Intelligent JSON-fixing algorithms and error fallback mechanisms are utilized to mitigate model hallucinations and formatting failures.

**Orchestration and Consultation:** For complex system generation tasks, the tool supports an orchestrator mode. This separates the planning phase from the execution phase, allowing a reasoning model to draft an architectural plan before passing exact specifications to a downstream coding model. Additionally, a consultation phase allows the active agent to query external, air-gapped LLMs to retrieve specific technical algorithms before resuming its local execution loop.

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

### Command-Line Arguments

**Path Targets**
*   `paths`: Specific files, directories, or `.zip` archives to include. Bypasses the full directory scan if provided.

**Filtering & Output Control**
*   `-l, --limit <int>`: Maximum recursion depth for scanning directories (default: 100).
*   `-f, --file_types <ext...>`: Space-separated file extensions to include (e.g., `-f py js html`).
*   `-e, --exclude <dir...>`: Space-separated directory names to exclude from the scan.
*   `-b, --batches <int>`: Number of batches to split large workspace context copies into (default: 1).
*   `--file-culling, --file-cull`: Enable file culling and AST map generation mode.

**Interactive & UI Modes**
*   `-s, --select`: Launch the interactive TUI selector to manually filter the context payload.
*   `--system`: Launch a TUI to inject system prompts and user instructions. Accepts an optional path to a custom text file.
*   `--web`: Launch the local Flask-based Web UI server on `127.0.0.1:5000`.

**Agent & Execution Modes**
*   `-a, --auto`: Run in continuous AI listener mode, monitoring the clipboard for execution payloads.
*   `-r, --revert`: Run the continuous listener mode, but reverse all incoming modifications.
*   `-o, --orchestrate`: Run in orchestrator mode to generate execution plans for downstream models.
*   `--cli`: Enable CLI Mode, allowing the LLM to output terminal commands in its payload.
*   `--consult`: Enable the consultation phase, permitting the AI to query external Expert LLMs.
*   `-x, --xml`: Instruct the AI to output XML payloads instead of JSON, bypassing quote-escaping vulnerabilities.
*   `-js, --json-select`: Parse a selection payload directly from the clipboard to automatically retrieve files/functions during the exploration phase.

**Environment Integrations**
*   `--web-apply`: Enable web macro mode. Translates AI execution payloads into simulated keyboard strokes for browser-based IDEs.
*   `--tfs`: Use TFVC (`tf.exe`) instead of Git for file checkout, addition, deletion, and check-in operations.

**Clipboard & File Outputs**
*   `--file`: Save the generated prompt to a temporary `.txt` file and copy the file object to the clipboard.
*   `--system-only`: Copy the raw system prompt text to the clipboard and exit.

---

## Supplementary Deployment Utilities

While `combineCopy` focuses on code generation and workspace modification, `ftpapp` and `webapp` are supplementary utilities developed to bridge Git-tracked repositories with restrictive, non-standard deployment environments. 

*   **`ftpapp`**: Designed for FTP-only environments, this tool calculates state changes by evaluating Git diffs between the current `HEAD` and a selected baseline commit. It synchronizes these modifications to the remote server using a background thread, preventing UI blocking while generating the required remote directory structures on the fly.
*   **`webapp`**: Designed for environments restricted to browser-based text editors where direct file uploads are unavailable. It identifies modifications via Git diffs and employs system-level OS hooks (via the `keyboard` module) to simulate deterministic overwrite macros, automating the transfer of local changes into the web interface.
