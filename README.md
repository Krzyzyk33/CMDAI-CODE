# 🚀 CMDAI CODE: The Fully Autonomous Terminal Assistant

**CMDAI CODE** (formerly CMDAI2) is a next-generation, locally hosted AI coding assistant designed to live natively within your terminal. 

While there are many AI coding assistants out there (like Cursor, Aider, or ChatGPT), CMDAI CODE takes a fundamentally different approach. It is built for developers who want **full control, zero vendor lock-in, and an AI that actually *thinks* and *verifies* its work before handing it to you.**

Forget about pasting broken code snippets back and forth. CMDAI CODE operates in a continuous, agentic loop, leveraging your local environment to write, test, and self-heal code entirely on its own.

---

## ✨ Key Features

### 🤖 Autonomous Auto-Testing (Self-Healing Loop)
Your agent will never hand you broken code again. Whenever CMDAI CODE writes or edits your `.py` or `.js` scripts, it silently runs the compiler or interpreter in the background to verify its work.
* **If it works:** You get the final result immediately.
* **If it fails (e.g., `SyntaxError`):** The model is temporarily denied control. It receives a strict system reprimand containing the error logs (stdout/stderr) and is forced to autonomously patch the code. The agent remains trapped in this self-healing loop until it executes perfectly!

### 🧠 Rolling Context (Smart Memory Compaction)
Say goodbye to *Out Of Memory (OOM)* crashes for massive 26B+ models. Running out of context window is the biggest bottleneck for AI agents. 
When your conversation history approaches 90% of your context window limit, CMDAI CODE automatically fires up a dedicated, smaller side-model (`compaction_model`). This model generates a concise summary of your session's progress, clears the raw bloated history, and frees up precious VRAM. Your powerful main model can then continue writing code indefinitely, with no loss of core context.

### 🌐 Zero Vendor Lock-in (Local & Cloud Agnostic)
You are completely free. You can instantly switch the "brain" of your assistant between:
* OpenAI 
* OpenRouter
* Blazing-fast free models on Groq & Cerebras
* **Fully local models via LocalLLMAPI/Ollama**

Native **Server-Sent Events (SSE) streaming** ensures your local GPU or cloud provider outputs tokens to the terminal in real-time without blocking the main thread.

### 🛡️ Tool Hallucination Prevention
No more broken JSON strings crashing your session. CMDAI CODE natively parses JSON function calls under the hood and draws an elegant interface using the `Rich` Python library. It also features **"soft" loop detection**: if the model gets stuck and uses a broken tool repeatedly, it receives a severe system prompt forcing it to change its strategy, rather than crashing your session.

### 💻 Native Local Execution
CMDAI CODE lives right in your environment. It automatically grabs context from your active IDE, searches through hundreds of files instantly (`grep_search`), scrapes the web for the latest documentation (`search_web`), and executes Python scripts or Bash commands directly on your machine.

---

## 🚀 Installation

Installing CMDAI CODE is effortless. We have provided an automated setup script that builds the package globally and safely migrates your old CMDAI2 data.

1. Clone or download this repository.
2. Open the project folder.
3. Run the setup script (Windows):
   ```cmd
   setup.bat
   ```
*(This script will run `pip install -e .`, add the directory to your PATH, and migrate your `~/.cmdai2` configuration to `~/.cmdai_code`).*

---

## 🛠️ Usage

Once installed, simply open a new Terminal or PowerShell window in any directory on your computer and type:

```cmd
cmdai code
```
*(Alternatively, you can also use `cmdai code`)*

The beautiful, `Rich`-powered UI will launch right in your terminal, ready to build!

---
*Clean code, absolute autonomy, and zero limits. Let AI write and test your project completely on its own!*
