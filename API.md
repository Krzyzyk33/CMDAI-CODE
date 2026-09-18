# 🌐 Supported API Providers & Configuration

CMDAI CODE supports 23 local and cloud model providers. You can run 100% offline local models (via GGUF, Ollama, or LM Studio) or connect to remote AI gateways.

---

## 1. Supported Providers Catalog

| Provider ID | Provider Name | Default / Curated Models | API Key Requirement | Key Signup URL |
| :--- | :--- | :--- | :--- | :--- |
| `local_gguf` | **Local GGUF (llama.cpp)** | Any `.gguf` in `models/` | **None (Offline)** | Built-in (run locally) |
| `opencode` | **OpenCode Zen** | `deepseek-v4-flash-free`, `qwen3.6-plus-free`, `mimo-v2.5-free`, `claude-3-7-sonnet` | **Optional** (Free models work without a key) | [opencode.ai/zen](https://opencode.ai/zen) |
| `openrouter` | **OpenRouter** | `anthropic/claude-3.7-sonnet`, `deepseek/deepseek-r1`, `openai/o3-mini` | **Optional / Required** for paid models | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `ollama` | **Ollama** | `llama3.3:latest`, `deepseek-r1:latest` | **None (Local)** | [ollama.com](https://ollama.com) |
| `lmstudio` | **LM Studio** | Local server models | **None (Local)** | [lmstudio.ai](https://lmstudio.ai) |
| `openai` | **OpenAI** | `o3-mini`, `o1`, `gpt-4o`, `gpt-4o-mini` | **Required** | [platform.openai.com](https://platform.openai.com/api-keys) |
| `anthropic` | **Anthropic** | `claude-3-7-sonnet-20250219`, `claude-3-5-sonnet`, `claude-3-5-haiku` | **Required** | [console.anthropic.com](https://console.anthropic.com/) |
| `gemini` | **Google Gemini** | `gemini-2.0-flash`, `gemini-2.0-pro-exp-02-05`, `gemini-1.5-pro` | **Required** | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `deepseek` | **DeepSeek** | `deepseek-reasoner` (R1), `deepseek-chat` (V3) | **Required** | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |
| `groq` | **Groq LPU** | `llama-3.3-70b-versatile`, `deepseek-r1-distill-llama-70b` | **Required** | [console.groq.com](https://console.groq.com/keys) |
| `cerebras` | **Cerebras** | `llama3.3-70b`, `llama3.1-8b` | **Required** | [cloud.cerebras.ai](https://cloud.cerebras.ai) |
| `xai` | **xAI (Grok)** | `grok-2-1212`, `grok-2-vision-1212` | **Required** | [console.x.ai](https://console.x.ai/) |
| `mistral` | **Mistral AI** | `codestral-latest`, `mistral-large-latest` | **Required** | [console.mistral.ai](https://console.mistral.ai/) |
| `together` | **Together AI** | `deepseek-ai/DeepSeek-R1`, `Qwen/Qwen2.5-Coder-32B-Instruct` | **Required** | [api.together.ai](https://api.together.ai/settings/api-keys) |
| `perplexity` | **Perplexity AI** | `sonar-pro`, `sonar-reasoning` | **Required** | [perplexity.ai](https://www.perplexity.ai/settings/api) |
| `cohere` | **Cohere** | `command-r-plus-08-2024`, `command-r-08-2024` | **Required** | [dashboard.cohere.com](https://dashboard.cohere.com/api-keys) |
| `fireworks` | **Fireworks AI** | `accounts/fireworks/models/deepseek-r1` | **Required** | [fireworks.ai](https://fireworks.ai/api-keys) |
| `sambanova` | **SambaNova Systems** | `Meta-Llama-3.3-70B-Instruct`, `DeepSeek-R1-Distill-Llama-70B` | **Required** | [cloud.sambanova.ai](https://cloud.sambanova.ai/apis) |
| `ai21` | **AI21 Labs** | `jamba-1.5-large`, `jamba-1.5-mini` | **Required** | [studio.ai21.com](https://studio.ai21.com/account/api-key) |
| `deepinfra` | **DeepInfra** | `deepseek-ai/DeepSeek-R1`, `meta-llama/Meta-Llama-3.3-70B-Instruct` | **Required** | [deepinfra.com](https://deepinfra.com/dash/api_keys) |
| `replicate` | **Replicate** | `meta/meta-llama-3.3-70b-instruct`, `deepseek-ai/deepseek-r1` | **Required** | [replicate.com](https://replicate.com/account/api-tokens) |
| `cloudflare` | **Cloudflare Workers AI** | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | **Required** | [dash.cloudflare.com](https://dash.cloudflare.com/) |
| `huggingface` | **Hugging Face** | `meta-llama/Llama-3.3-70B-Instruct`, `deepseek-ai/DeepSeek-R1` | **Required** | [huggingface.co](https://huggingface.co/settings/tokens) |

---

## 2. Configuring API Keys

### Method A: Interactive TUI (`/models` + `Ctrl+D`)
1. In the chat prompt, type `/models` and press <kbd>Enter</kbd>.
2. Use arrow keys (<kbd>↑</kbd> / <kbd>↓</kbd>) to highlight any provider (e.g., `OpenCode Zen`, `OpenRouter`, `Groq`).
3. Press <kbd>Ctrl</kbd> + <kbd>D</kbd> to open the API Key dialog.
4. Paste your API key and press <kbd>Enter</kbd>. The key is securely saved to your local `config.json`.

### Method B: Direct `config.json`
Add your keys to the `api_keys` dictionary in `config.json` (which is git-ignored):

```json
{
  "default_provider": "opencode",
  "default_model": "deepseek-v4-flash-free",
  "api_keys": {
    "opencode": "optional-opencode-zen-key-here",
    "openrouter": "sk-or-v1-...",
    "openai": "sk-proj-...",
    "anthropic": "sk-ant-..."
  }
}
```

### Method C: Command-Line Arguments (CLI)
Override the active provider and model directly when launching:

```powershell
# Run with OpenCode Zen free model (no key required):
.\cmdai.bat --provider opencode --model deepseek-v4-flash-free

# Run with Claude 3.7 on OpenRouter:
.\cmdai.bat --provider openrouter --model anthropic/claude-3.7-sonnet

# Run with offline local model:
.\cmdai.bat --provider local_gguf --model Qwable-9B-Claude-Fable-5-Q4_K_M.gguf
```
