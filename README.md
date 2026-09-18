# ⌬ CMDAI CODE: Autonomous Terminal Coding Agent

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg?logo=windows&logoColor=white)](https://microsoft.com)
[![UI](https://img.shields.io/badge/UI-Modern%20TUI-58a6ff.svg)](https://textual.textualize.io/)
[![Local Engine](https://img.shields.io/badge/Local%20Engine-llama.cpp%20(GGUF)-7ee787.svg)](https://github.com/ggerganov/llama.cpp)
[![License](https://img.shields.io/badge/License-Custom%20License-orange.svg)](LICENSE)
[![Code of Conduct](https://img.shields.io/badge/Policy-Code%20of%20Conduct-blue.svg)](CODE_OF_CONDUCT.md)

**CMDAI CODE** is a next-generation autonomous coding agent and programming assistant built specifically for the Windows terminal. It seamlessly combines full reasoning and multi-file code editing autonomy with an elegant Dark Theme terminal UI, offline local GGUF model execution (100% privacy), and integration with major cloud AI providers.

</div>

---

## 🌟 Key Features

### 1. 🎨 Modern Dark Terminal Interface
- High-contrast developer theme (`#090d13` / `#0d1117`) with distinct user and assistant message blocks.
- Real-time text and code streaming (~33 FPS).
- Braille spinner animations and discrete tool execution indicators (`●` completed, `○` pending).
- Intelligent `ThinkingBlock` with collapsible reasoning trace and automatic hiding for non-reasoning models.
- Smooth mouse wheel scrolling with zero bottom-bounce.

### 2. 🔍 Zero-Wrap Code Shift (`ToolBlock`)
- Code viewing and editing widgets (`edit`, `read`, `write`) enforce strict `text-wrap: nowrap` and `text-overflow: clip`.
- Code lines **never fold or wrap**, preserving syntax indentation and structure.
- Hovering over an expanded tool block and pressing the `→` or `←` arrow keys shifts the view horizontally to reveal long lines, with a tight 1-space padding margin past the last character.
- Collapsing a tool block immediately resets horizontal scroll offset back to the beginning.

### 3. 🛡️ Autonomous Test-and-Repair Loop
- The built-in test runner automatically detects project test suites (`pytest`, `npm test`, `cargo test`, `go test`).
- Code changes trigger AST syntax validation and background test verification.
- When an error or failure is detected, the agent autonomously self-corrects the code without halting.

### 4. 🌳 Real Git Integration (`/branch`, `/diff`, `/commit`)
- Modal `/branch` connects directly to system Git, displaying branches and commit history.
- Built-in Git repository initialization (`git init`) for new workspaces.
- Standalone code editor (`/editor`) and split-view diff viewer with protected change approval.

### 5. ⚡ Unbroken Continuity & Context Compaction
- **Uncapped action loop**: The agent executes multi-step workflows (inspect → plan → edit multiple files → test → verify) until completion.
- **Safe clipboard**: <kbd>Ctrl</kbd>+<kbd>C</kbd> safely copies expanded code blocks to the clipboard without crashing the application.
- **Dynamic context compaction**: Intelligent token budgeting ensures prompt and generation never exceed model context windows (`exceed context window` prevention).

---

## 🚀 Quick Setup & Installation

### Step 1: Clone the Repository
```powershell
git clone https://github.com/Krzyzyk33/CMDAI-CODE.git
cd CMDAI-CODE
```

### Step 2: Run the Installer
Launch the automated setup script:
```cmd
install.bat
```
*(or run `installer.bat`)*

The installer automatically:
1. Verifies Python 3.10+ and system prerequisites.
2. Installs required packages from `requirements.txt`.
3. Creates a starter `config.json` from `config.example.json`.

### Step 3: Launch CMDAI CODE
Start the assistant by running:
```powershell
.\cmdai.bat
```
or:
```powershell
python cmdai.py
```

---

## 💻 Windows Terminal Commands (CLI)

CMDAI CODE provides dedicated CLI commands:

| Command | Description |
| :--- | :--- |
| `cmdai` / `cmdai.bat` | Launches the terminal UI in the current workspace directory. |
| `cmdai code update` | Safely updates CMDAI CODE from GitHub and synchronizes dependencies. |
| `cmdai code addlocal model` | Opens Windows File Explorer to select a local `*.gguf` model and copies it into `models/`. |
| `cmdai editor [file]` | Opens the built-in code editor in a standalone console window. |
| `install.bat` | Fast installer for dependencies and initial configuration. |

---

## ⌨️ In-Chat Slash Commands

Type `/` in the prompt input to open the interactive autocomplete menu:

| Command | Description |
| :--- | :--- |
| `/help` | Displays keyboard shortcuts, commands reference, and guide. |
| `/models` | Model selector for local GGUF models, OpenCode Zen, and 23+ cloud AI providers (see [API.md](API.md)). |
| `/branch` | Git branch manager with commit history and branch switching/creation. |
| `/diff` | Opens modified files review and syntax-highlighted diff inspector. |
| `/commit` | Git commit creator with message input and commit execution. |
| `/plan` | Interactive task list (TODO tracker) updated live during generation. |
| `/editor` | Opens the terminal code editor in a separate console window. |
| `/add` | Opens the project folder in Windows Explorer or attaches files to context. |
| `/sessions` | Searchable chat session manager with filtering by title and date. |
| `/context` | RAM/VRAM resource statistics and active token count monitors. |
| `/thinking` | Toggle thinking/reasoning effort (`Off`, `Low`, `Medium`, `High`). |
| `/test` | Manually run project test suites with formatted test reports. |
| `/compact` | Manually trigger natural context summarization and history compaction. |

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| <kbd>Ctrl</kbd> + <kbd>T</kbd> | Toggle expand/collapse for all tool blocks (`ToolBlock`). |
| <kbd>Ctrl</kbd> + <kbd>C</kbd> | Copy selected code to clipboard or interrupt generation. |
| <kbd>Esc</kbd> / <kbd>Esc</kbd> <kbd>Esc</kbd> | Close active modal / cancel ongoing model turn. |
| <kbd>↑</kbd> / <kbd>↓</kbd> (in empty input) | Navigate prompt history. |
| <kbd>←</kbd> / <kbd>→</kbd> (hovering over code) | Horizontally shift long lines of code in narrow terminal windows. |
| <kbd>Ctrl</kbd> + <kbd>Q</kbd> | Cleanly exit application. |

---

## 🏗️ Project Architecture

```text
CMDAI CODE/
├── cmdai.py                     # Main CLI entrypoint and command router
├── cmdai.bat                    # Windows startup launcher
├── install.bat                  # Automated dependency and environment installer
├── installer.bat                # Compatible alias forwarding to install.bat
├── editor.bat                   # Standalone code editor launcher
├── config.example.json          # Starter configuration template
├── requirements.txt             # Core Python package dependencies
├── README.md                    # Project documentation
├── API.md                       # Complete API providers, OpenCode Zen, and gateway guide
├── CODE_OF_CONDUCT.md           # Community guidelines and contribution policy
├── LICENSE                      # Proprietary custom license and terms of use
├── .gitignore                   # Local configuration and cache exclusion rules
├── app/
│   └── sessions/                # Local conversation history storage
└── src/
    └── cmdai/
        ├── agent/               # Autonomous agent runner, tools, and subagents
        ├── core/                # Engine, model worker, resource limits, and sessions
        ├── editor/              # Integrated terminal code editor and diff viewer
        ├── grammars/            # GBNF tool-calling grammars for llama.cpp
        ├── indexer/             # AST code indexer and symbol database
        └── tui/                 # Terminal UI application, widgets, and modals
```

---

## 📄 License & Terms of Use

**Custom Proprietary License — All Rights Reserved.**

- **Permitted**: You are welcome to propose changes, enhancements, and bug fixes directly to this official repository ([Pull Requests](https://github.com/Krzyzyk33/CMDAI-CODE/pulls)).
- **Prohibited**: Creating derivative works, standalone forks, commercial packaging, or secondary distributions based on this codebase is **strictly prohibited without prior written authorization** from the copyright holder.

For complete legal terms and contribution rules, refer to [LICENSE](LICENSE) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
