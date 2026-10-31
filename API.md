# 🌐 Supported API Providers & Configuration

CMDAI CODE supports 23 local and cloud model providers. You can run 100% offline local models (via GGUF, Ollama, or LM Studio) or connect to remote AI gateways.

---

## 1. Supported Providers Catalog

| Provider ID | Provider Name | Default / Curated Models | API Key Requirement | Key Signup URL |
| :--- | :--- | :--- | :--- | :--- |
| `local_gguf` | **Local GGUF (Vulkan/CPU)** | Any `.gguf` in `models/` (auto-detected) | **None (Offline)** | Built-in (run locally) |
| `opencode` | **OpenCode Zen** | `deepseek-v4-flash-free`, `mimo-v2.5-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`, `ling-3.0-flash-fin-free`, `jev-1.13-free`, `muse-spark-1.3-contributor-free`, `qwen3.6-plus-free`, `minimax-m3-free`, `big-pickle`, `claude-3-7-sonnet`, `gpt-4o` | **Required** (free tier available) | [opencode.ai/zen](https://opencode.ai/zen) |
| `openrouter` | **OpenRouter** | `google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free`, `deepseek/deepseek-r1:free`, `qwen/qwen-2.5-coder-32b-instruct:free`, `mistralai/mistral-7b-instruct:free`, `anthropic/claude-3.7-sonnet`, `openai/o3-mini` | **Required** (free account, free models available) | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `ollama` | **Ollama (Local)** | Auto-fetched from `http://localhost:11434` (`/api/tags`) | **None (Local)** | [ollama.com](https://ollama.com) |
| `lmstudio` | **LM Studio (Local)** | Auto-fetched from `http://localhost:1234/v1` | **None (Local)** | [lmstudio.ai](https://lmstudio.ai) |
| `vllm` | **vLLM Server** | Auto-fetched from `http://localhost:8000/v1` | **None (Local)** | Built-in (self-hosted) |
| `openai` | **OpenAI** | `o3-mini`, `o1`, `gpt-4o`, `gpt-4o-mini` | **Required** | [platform.openai.com](https://platform.openai.com/api-keys) |
| `anthropic` | **Anthropic** | `claude-3-7-sonnet-20250219`, `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022` | **Required** | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `gemini` | **Google Gemini** | `gemini-2.0-flash`, `gemini-2.0-pro-exp-02-05`, `gemini-1.5-pro` | **Required** | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `deepseek` | **DeepSeek** | `deepseek-reasoner` (R1), `deepseek-chat` (V3) | **Required** | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |
| `groq` | **Groq LPU** | `llama-3.3-70b-versatile`, `deepseek-r1-distill-llama-70b`, `llama-3.1-8b-instant` | **Required** | [console.groq.com](https://console.groq.com/keys) |
| `cerebras` | **Cerebras** | `llama3.3-70b`, `llama3.1-8b` | **Required** | [cloud.cerebras.ai](https://cloud.cerebras.ai) |
| `xai` | **xAI (Grok)** | `grok-2-1212`, `grok-2-vision-1212` | **Required** | [console.x.ai](https://console.x.ai/) |
| `mistral` | **Mistral AI** | `mistral-large-latest`, `codestral-latest`, `ministral-8b-latest` | **Required** | [console.mistral.ai](https://console.mistral.ai/api-keys/) |
| `together` | **Together AI** | `deepseek-ai/DeepSeek-R1`, `meta-llama/Llama-3.3-70B-Instruct-Turbo`, `Qwen/Qwen2.5-Coder-32B-Instruct` | **Required** | [api.together.ai](https://api.together.ai/settings/api-keys) |
| `perplexity` | **Perplexity AI** | `sonar-pro`, `sonar`, `sonar-reasoning` | **Required** | [perplexity.ai](https://www.perplexity.ai/settings/api) |
| `cohere` | **Cohere** | `command-r-plus-08-2024`, `command-r-08-2024` | **Required** | [dashboard.cohere.com](https://dashboard.cohere.com/api-keys) |
| `fireworks` | **Fireworks AI** | `accounts/fireworks/models/deepseek-r1`, `accounts/fireworks/models/llama-v3p3-70b-instruct` | **Required** | [fireworks.ai](https://fireworks.ai/api-keys) |
| `sambanova` | **SambaNova Systems** | `Meta-Llama-3.3-70B-Instruct`, `DeepSeek-R1-Distill-Llama-70B` | **Required** | [cloud.sambanova.ai](https://cloud.sambanova.ai/apis) |
| `ai21` | **AI21 Labs** | `jamba-1.5-large`, `jamba-1.5-mini` | **Required** | [studio.ai21.com](https://studio.ai21.com/account/api-key) |
| `deepinfra` | **DeepInfra** | `deepseek-ai/DeepSeek-R1`, `meta-llama/Meta-Llama-3.3-70B-Instruct` | **Required** | [deepinfra.com](https://deepinfra.com/dash/api_keys) |
| `replicate` | **Replicate** | `meta/meta-llama-3.3-70b-instruct`, `deepseek-ai/deepseek-r1` | **Required** | [replicate.com](https://replicate.com/account/api-tokens) |
| `cloudflare` | **Cloudflare Workers AI** | `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | **Required** | [dash.cloudflare.com](https://dash.cloudflare.com/profile/api-tokens) |
| `huggingface` | **Hugging Face Inference** | Any Hugging Face Inference model (no curated list, remote fetch disabled) | **Required** | [huggingface.co](https://huggingface.co/settings/tokens) |
| `zai` | **Z.AI (Zhipu)** | `glm-4.6`, `glm-4.5`, `glm-4.5-air` | **Required** | [z.ai](https://z.ai/manage-apikey/apikey-list) |
| `github_models` | **GitHub Models** | `openai/gpt-4o`, `openai/gpt-4o-mini`, `meta/Llama-3.3-70B-Instruct`, `deepseek/DeepSeek-R1` | **Required** | [github.com](https://github.com/settings/tokens) |
| `moonshot` | **Moonshot AI** | `kimi-k2-0711-preview`, `moonshot-v1-8k`, `moonshot-v1-32k` | **Required** | [platform.moonshot.ai](https://platform.moonshot.ai/console/api-keys) |
| `nvidia` | **NVIDIA NIM** | `meta/llama-3.3-70b-instruct`, `deepseek-ai/deepseek-r1`, `qwen/qwen2.5-coder-32b-instruct` | **Required** | [build.nvidia.com](https://build.nvidia.com) |
| `nebius` | **Nebius AI Studio** | `meta-llama/Meta-Llama-3.3-70B-Instruct`, `deepseek-ai/DeepSeek-R1`, `Qwen/Qwen2.5-Coder-32B-Instruct` | **Required** | [studio.nebius.com](https://studio.nebius.com/settings/api-keys) |
| `minimax` | **MiniMax** | `MiniMax-M2`, `MiniMax-Text-01` | **Required** | [platform.minimax.io](https://platform.minimax.io) |
| `bedrock` | **AWS Bedrock** | `anthropic.claude-3-7-sonnet-20250219-v1:0`, `meta.llama3-3-70b-instruct-v1:0` | **Special auth** (AWS SigV4, not Bearer key) | [console.aws.amazon.com/bedrock](https://console.aws.amazon.com/bedrock) |
| `vertex` | **Google Vertex AI** | `gemini-2.0-flash-001`, `claude-3-7-sonnet@20250219` | **Special auth** (GCP access token, not plain key) | [console.cloud.google.com/vertex-ai](https://console.cloud.google.com/vertex-ai) |
| `copilot` | **GitHub Copilot** | `gpt-4o`, `claude-3.7-sonnet` | **Special auth** (GitHub OAuth device-flow, not PAT) | [github.com/settings/copilot](https://github.com/settings/copilot) |

> Note: alias `opencode_zen` is auto-mapped to `opencode` in code.

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
