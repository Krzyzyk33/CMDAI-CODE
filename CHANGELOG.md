# CHANGELOG

Generowane z GitHub Releases (>= v3.0-alpha).

## [v3.0.1-alpha] - 2026-09-20
### CMDAI CODE v3.0.1-alpha
## What's New

* More models: 23 -> 33 providers (including Z.AI, GitHub Models, Moonshot, NVIDIA NIM, Nebius, MiniMax, AWS Bedrock, Vertex AI, Copilot).
* OpenCode Zen now requires an API key, but free models are pinned to the top of the list and provide better error reporting.
* Custom models: added `+ Add model` option in `/models` for Ollama / HuggingFace / others, removing the rigid pre-defined list.
* MCP: support for external tool servers (`mcp_config.example.json`, autostart, status preview).
* In-app Changelog: release list fetched from GitHub (`>= v3.0-alpha`) + post-update notification. `--alpha`
* Cleaner terminal: dark inputs in modals, fluid thinking indicators `[⌬, ✻]`, no-wrap code blocks (`→/←`), Todo bar, side-by-side diff view.
* Sessions are now strictly stored in `app/sessions/`.
* New tools added in `tools/`.
* New `vision` and `screenshot` tools are available exclusively for vision-capable models.
* Fixed forced limit removed (can be restored in `/parms`); only the context limit calculated by the following equation remains:

```math
L_{\text{ctx}} = \max \left( 2048, \; \min \left( L_{\text{train}}, \; \left\lfloor \frac{0.70 \cdot (\text{RAM}_{\text{available}} - M_{\text{model}})}{2 \cdot n_{\text{layer}} \cdot n_{\text{head\_kv}} \cdot n_{\text{embd\_head\_k}} \cdot 2} \right\rfloor \right) \right)
```
* Added CLI application commands to run directly from the terminal without entering the app interface, e.g., `cmdai code update`, etc. `--alpha`

## Important

* After updating, re-add your Ollama / HF models using the `+ Add model` button.
* Potential context errors and summarization issues may occur with some models.
* Potential scroll bugs.

## Installation

```powershell
git clone [https://github.com/Krzyzyk33/CMDAI-CODE.git](https://github.com/Krzyzyk33/CMDAI-CODE.git)
cd CMDAI-CODE
.\install.bat
.\cmdai.bat

[v3.0.1-alpha](https://github.com/Krzyzyk33/CMDAI-CODE/releases/tag/v3.0.1-alpha)

## [v3.0-alpha] - 2026-09-18
### CMDAI CODE v3.0-alpha
CMDAI CODE v3.0-alpha is a major milestone release delivering a completely redesigned Dark Terminal UI, dedicated session management, native OpenCode Zen integration with free cloud models, offline GGUF local model execution, multi-agent orchestration, and real-time Git developer tooling.

---

## 🎨 1. Brand-New Modern Dark Terminal UI & Visual Experience

- **Developer Dark Theme**: High-contrast, clean developer color palette (`#090d13` / `#0d1117`) with distinct visual cards for user prompts, model responses, and system status badges.
- **Zero-Wrap Code Shift (`ToolBlock`)**: Code view and edit blocks enforce strict `text-wrap: nowrap` to ensure indentation and formatting are never altered. Smooth horizontal panning with <kbd>→</kbd> / <kbd>←</kbd> arrow keys with a clean 1-character boundary past the longest line, plus instant position reset upon collapse.
- **Dynamic Reasoning & Thinking Visualizer**: Dedicated `ThinkingBlock` showing live thought progress with signature alternating glyph animations (`[⌬, ✻]`). Automatically collapses upon completion and hides completely for non-reasoning models.
- **Real-Time Text Streaming**: Smooth text and code token delivery (~33 FPS) with zero bottom-bounce or scroll stutter.
- **Interactive Multi-Step Modals**:
  - `/models` — Interactive grid selector for local GGUF models, OpenCode Zen, and cloud providers with instant filter search.
  - `/sessions` — Searchable conversation history browser with real-time text query filtering.
  - `/branch` — Visual Git branch manager with commit log preview and branch switching/creation.
  - `/diff` — Side-by-side and unified syntax-highlighted diff inspector for modified files.
  - `/commit` — In-terminal Git commit composer with staged changes review.
  - `/params` — Dynamic generation settings (temperature, top_p, max_tokens, reasoning effort).
  - `/context` — Live RAM, VRAM, context window, and token usage monitors.
  - Interactive `AskModal` — Question stepper with multiple-choice keyboard shortcuts (<kbd>1</kbd>–<kbd>9</kbd>), custom write-in input, and final review confirmation.
- **Safe Clipboard Integration**: Copy code blocks with <kbd>Ctrl</kbd>+<kbd>C</kbd> cleanly without interrupting or crashing the terminal process.

---

## 🧠 2. Autonomous Agent Engine & Tooling Ecosystem

- **Uncapped Autonomous Execution Loop**: Executes end-to-end multi-step tasks (analyzing workspace → planning → multi-file editing → testing → verification) until completion.
- **Comprehensive Built-in Tool Suite**:
  - `read` — Smart file inspection with line-range slicing and binary file detection.
  - `edit` — Exact contiguous pattern replacements with AST syntax verification.
  - `write` — Safe file creation with directory tree auto-provisioning.
  - `command` — Asynchronous terminal command runner with background job management.
  - `ls` / `glob` / `search` — Fast ripgrep and directory indexing across complex workspaces.
  - `ask` — User clarification modal with interactive response choices.
- **Autonomous Test-and-Repair Loop**: Automatically detects test runners (`pytest`, `npm test`, `cargo test`, `go test`), executes test suites upon file edits, and autonomously self-heals errors.
- **Subagents Orchestration Queue**: Automatically decomposes complex prompts into specialized autonomous roles (e.g. Architect, Reviewer, Analyst) executed concurrently for API models or sequentially to conserve local RAM/VRAM.
- **GBNF Grammar Constraints**: Strict grammar enforcement for local models to guarantee valid tool-call syntax and eliminate hallucinated JSON parameters.

---

## 📁 3. Dedicated `app/sessions` Architecture & Auto-Migration

- **Centralized Session Storage**: All conversations, tool states, and token metrics are persistently saved directly to `app/sessions/<session_id>.json`.
- **Automatic Multi-Directory Migration**: On startup, CMDAI CODE scans historical paths (`.cmdai/sessions`, `conversations/`, user profiles) and automatically migrates all existing sessions into `app/sessions/`.
- **Git Security & Privacy Protection**: The `app/sessions` directory structure is tracked via `.gitkeep`, while personal session data files (`app/sessions/*.json`) and personal configs are excluded in `.gitignore` to prevent confidential chat history leaks.

---

## ⚡ 4. OpenCode Zen Integration & 23+ Providers Catalog

- **Native OpenCode Zen Gateway**: Built-in support for OpenCode Zen, offering free high-capability coding and reasoning models (MiniMax-01, GLM-4-9B, Qwen-2.5-Coder) with **no API key required**.
- **Complete `API.md` Reference**: Exhaustive setup manual covering endpoints, model identifiers, API keys, and environment variables across 23+ major AI providers:
  - OpenCode Zen, OpenAI, Anthropic, Google Gemini, Groq, Mistral AI, DeepSeek, xAI (Grok), OpenRouter, Together AI, Perplexity, Cohere, Fireworks AI, Cerebras, Sambanova, Ollama, and local GGUF offline execution.
- **Dynamic Context Compaction**: Smart token budgeting that monitors token usage and triggers natural summarization before context limits are reached.

---

## 💻 5. Standalone Tools & Windows CLI Infrastructure

- **Automated Windows Installers**: `install.bat` and `installer.bat` for one-step dependency installation, Python environment verification, and initial configuration.
- **Global Terminal Commands**:
  - `cmdai` / `cmdai.bat` — Launches the full terminal assistant in the current directory.
  - `cmdai code update` — Pulls latest updates directly from GitHub and syncs dependencies.
  - `cmdai code addlocal model` — Opens Windows File Explorer to pick local `.gguf` weights and imports them into `models/`.
  - `cmdai editor [file]` / `editor.bat` — Opens the built-in standalone terminal code editor.
- **Automated Crash Logging**: Automatic error diagnosis written to `logs/cmdai_crash.log` ensuring zero silent crashes.

---

## 🔒 6. Proprietary Custom License & Code of Conduct

- **Custom Legal Protection**: Proprietary terms prohibiting unauthorized forks, standalone clones, or commercial redistribution without prior written authorization.
- **Welcoming Upstream Contributions**: Clear framework allowing users to submit pull requests, bug fixes, and feature proposals directly to the official repository.

---

## 🚀 Quick Setup

```powershell
# Clone the repository
git clone https://github.com/Krzyzyk33/CMDAI-CODE.git
cd CMDAI-CODE

# Run automated installer
.\install.bat

# Launch CMDAI CODE
.\cmdai.bat

[v3.0-alpha](https://github.com/Krzyzyk33/CMDAI-CODE/releases/tag/v3.0-alpha)
