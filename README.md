# AI-Tools

This repository provides a suite of command-line utilities. They bridge the gap between your local development workspace and Large Language Models (LLMs).

## Installation

This repository is configured as a Python package. Install it locally to expose the tools globally on your command line.

```bash
git clone https://github.com/OzanKutlar/AI-Tools
cd AI-Tools
pip install -e .
```

Once installed, you can use the `combineCopy`, `ftpapp`, and `webapp` commands. You also get the `app` shortcut, which automatically runs `combineCopy -a`.

## combineCopy: Context Assembly and Execution Agent

`combineCopy` is designed to eliminate the manual overhead of moving code between your IDE and an AI. It handles two main jobs: gathering context and executing code.

Originally created to help developers easily select and place files into context for AI tools, `combineCopy` solves a major limitation of web-based AI platforms. Many of these platforms treat uploaded documents as compressed knowledgebases and cannot process the entirety of the document at once. `combineCopy` bypasses this by allowing you to directly post complete files into the chat box, ensuring the LLM sees all the files and keeps them fully in context.

As development needs increased, the tool evolved. It began appending automatic system prompts to the end of copied files, packaging the user prompt together with it. Eventually, capabilities were added allowing the tool to request files on its own and make other agent calls, slowly transforming `combineCopy` into a full agentic harness.

### Context Assembly

LLMs need precise context to write good code. `combineCopy` gets it for them.

It scans your workspace, filters extensions, and drops excluded directories. It even unpacks `.zip` archives on the fly.

To keep your token counts low, it uses file culling. It builds an Abstract Syntax Tree (AST) map of your project. The AI gets the blueprint of your codebase without having to read every single line of code.

### Automated Execution

Manual copy-pasting is slow and prone to errors. The execution agent fixes this.

Instead of letting the AI output the entire file, which eats up precious output tokens and slows down generation, we make the AI execute targeted search-and-replace modifications inside the files. This allows the LLM to efficiently fix problems on its own.

It monitors your clipboard in the background. When it catches a valid JSON or XML instruction payload, it goes to work.
It creates files, modifies code using targeted search-and-replace, and executes CLI commands. You see the diffs on your screen before anything becomes permanent.

### Rehab Mode (Active Learning)

Relying entirely on AI agents to write code can cause your "muscle memory" and problem-solving skills to atrophy. Rehab Mode combats this.

When running in Rehab Mode, the AI explains the *logical intent* behind its modifications in plain English, but the actual code is initially hidden from you.
1. The tool presents the plain-English instructions and hints to you.
2. You press a button to open your local editor and attempt to write the code yourself based on the instructions.
3. You press another button to open Meld, which compares your handwritten code against the AI's intended code.
4. Once you verify or correct your code, you apply the change.

If you get stuck, you can reveal hints progressively or fully reveal the AI's exact code. You can launch Rehab mode globally with the `--rehab` flag to force the AI to write instructions, or you can invoke it on-the-fly in the standard agent listener by selecting a pending file and pressing `t` (Practice).

### Orchestration (Experimental)

Need a massive refactor? Run the orchestrator mode. A reasoning model builds the architectural plan, and downstream models write the actual code. Note that this orchestration mode is currently experimental.

### External LLM Consult

If the AI gets stuck on a complex problem, it can trigger a consultation phase. It pauses, queries an external expert model, and brings the answers back into your local loop.

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

#### Path Targets

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `paths` | Specific files, directories, or `.zip` archives to include. Bypasses the full directory scan if provided. | none | none |

#### Filtering & Output Control

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `--limit <int>` | Maximum recursion depth for scanning directories. | 100 | -l |
| `--file_types <ext...>` | Space-separated file extensions to include (e.g., `py js html`). | none | -f |
| `--exclude <dir...>` | Space-separated directory names to exclude from the scan. | none | -e |
| `--batches <int>` | Number of batches to split large workspace context copies into. | 1 | -b |
| `--file-culling`, `--file-cull` | Enable file culling and AST map generation mode. | false | none |
| `--diff` | Inject current uncommitted git diff directly into the prompt context. | false | -d |

#### Interactive & UI Modes

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `--select` | Launch the interactive TUI selector to manually filter the context payload. | false | -s |
| `--system` | Launch a TUI to inject system prompts and user instructions. Accepts an optional path to a custom text file. | none | none |
| `--web` | Launch the local Flask-based Web UI server on `127.0.0.1:5000`. | false | none |

#### Agent & Execution Modes

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `--auto` | Run in continuous AI listener mode, monitoring the clipboard for execution payloads. | false | -a |
| `--rehab` | Enable Active Learning mode. Forces the AI to emit plain-English instructions and hints, hiding the code until you practice writing it yourself. | false | none |
| `--revert` | Run the continuous listener mode, but reverse all incoming modifications. | false | -r |
| `--orchestrate` | Run in orchestrator mode to generate execution plans for downstream models. | false | -o |
| `--cli` | Enable CLI Mode, allowing the LLM to output terminal commands in its payload. | false | none |
| `--consult` | Enable the consultation phase, permitting the AI to query external Expert LLMs. | false | none |
| `--xml` | Instruct the AI to output XML payloads instead of JSON, bypassing quote-escaping vulnerabilities. | false | -x |
| `--json-select` | Parse a selection payload directly from the clipboard to automatically retrieve files/functions during the exploration phase. | false | -js |

#### Environment Integrations

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `--web-apply` | Enable web macro mode. Translates AI execution payloads into simulated keyboard strokes for browser-based IDEs. | false | none |
| `--tfs` | Use TFVC (`tf.exe`) instead of Git for file checkout, addition, deletion, and check-in operations. | false | none |

#### Clipboard & File Outputs

| Option | Description | Default | Alias |
| :--- | :--- | :--- | :--- |
| `--file` | Save the generated prompt to a temporary `.txt` file and copy the file object to the clipboard. | false | none |
| `--system-only` | Copy the raw system prompt text to the clipboard and exit. | false | none |

---

## Supplementary Deployment Utilities

Sometimes you have to deploy code without Git. These secondary tools handle restrictive environments.

*   **`ftpapp`**: Syncs your workspace to FTP servers. It reads your Git history, finds exactly what changed since your last commit, and transfers only those files. It runs in the background so your terminal stays responsive.
*   **`webapp`**: Built for browser-based IDEs where direct uploads fail. It reads your Git diffs, hooks into your OS keyboard, and physically macros the file updates into the browser for you.
