import json
import re
from typing import List, Dict, Any, Generator, Tuple
from openai import OpenAI
import os


class OpenAIAPIModel:
    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        provider_id: str = None,
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key

        from src.providers import detect_provider_by_url, get_provider

        if not provider_id:
            provider_id = detect_provider_by_url(base_url)
        self.provider_module = get_provider(provider_id)

        if self.provider_module and hasattr(self.provider_module, "get_client"):
            self.client = self.provider_module.get_client(api_key)
        else:
            self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.is_api = True

    def _needs_tool_injection(self) -> bool:
        broken_models = ["ornith", "qwythos", "qwynthos"]
        return any(m in self.model_name.lower() for m in broken_models)

    def unload(self):
        import requests
        from urllib.parse import urlparse

        if (
            "127.0.0.1" in self.base_url
            or "localhost" in self.base_url
            or "192.168." in self.base_url
        ):
            try:
                parsed = urlparse(self.base_url)
                host = parsed.hostname
                port = 8000
                scheme = parsed.scheme or "http"
                url = f"{scheme}://{host}:{port}/api/unload"

                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                payload = {"model": self.model_name}
                requests.post(url, json=payload, headers=headers, timeout=5)
            except Exception:
                pass

    def _build_tool_system_prompt(self, tools: list) -> str:
        import json

        tool_defs = json.dumps(tools, indent=2, ensure_ascii=False)
        return f"""You have access to the following tools. When you decide to call a tool, your ENTIRE response must be exactly one fenced JSON block in this exact format — nothing before it, nothing after it:

```json
{{"name": "<tool_name>", "arguments": {{<parameters>}}}}
```

Available tools:
{tool_defs}

RULES:
1. Call at most ONE tool per response. Never output more than one JSON object.
2. If you are not calling a tool, do NOT output any JSON block — just respond normally in plain text.
3. The JSON must be strictly valid and parseable: double quotes only, no trailing commas, no comments, no single quotes.
4. When calling a tool, output nothing except the ```json``` block above — no preamble, no explanation, no text like "Calling tool:", no summary after it.
5. "arguments" must contain exactly the parameters defined for that tool: correct types, all required parameters present, and no invented parameters that aren't in the tool's definition.
6. Never split a single tool call across multiple JSON blocks or mix a tool call with regular text in the same response."""

    def get_context_limit(self) -> int:
        return 10000000

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]] = None,
        reasoning_budget: int = 16384,
    ) -> Generator[Tuple[str, str, Any], None, None]:

        openai_tools = None
        if tools:
            openai_tools = [
                {"type": "function", "function": t["function"]} for t in tools
            ]
        active_tool_calls = {}
        in_thinking = False
        content_buffer = ""
        full_content_str = ""
        try:
            if openai_tools and self._needs_tool_injection():
                tool_injection = self._build_tool_system_prompt(openai_tools)
                if messages and messages[0]["role"] == "system":
                    messages[0]["content"] = (
                        tool_injection + "\n\n" + messages[0]["content"]
                    )
                else:
                    messages.insert(0, {"role": "system", "content": tool_injection})

                if messages and messages[-1]["role"] == "user":
                    warning = "\n\n[SYSTEM NOTE: If you decide to call a tool this turn, think it through first inside a <think> block before generating the ```json``` call — but you do not have to include a <think> block every single time. Skip it for simple, obvious actions where no real analysis is needed. If you do use <think>, always finish and close the block before outputting the tool call — never call a tool while still reasoning inside it.]"
                    messages[-1]["content"] += warning

                openai_tools = None

            if "127.0.0.1" in getattr(self, "base_url", "") or "localhost" in getattr(
                self, "base_url", ""
            ):
                sys_content = messages[0].get("content", "") if messages else ""
                req = ""
                if "THINKING BEHAVIOR - EXTREME" in sys_content:
                    req = "\n\n[FORMATTING REQUIREMENT: This session uses EXTREME thinking depth. Expand your reasoning fully. Inside your <think> block, use the exact prefixes: |_ UNDERSTAND:, |_ CONTEXT:, |_ OPTIONS:, |_ CHOICE:, |_ RISK:, and |_ PLAN:. Build a deep, multi-level tree, not free-form paragraphs. At this level, ALWAYS include a full <think> block before any tool call or final answer — the 'skip thinking for simple actions' allowance does not apply here.]"
                elif "THINKING BEHAVIOR - ULTRA" in sys_content:
                    req = "\n\n[FORMATTING REQUIREMENT: This session uses ULTRA thinking depth. Inside your <think> block, use the exact prefixes: |_ UNDERSTAND:, |_ CONTEXT:, |_ OPTIONS:, |_ CHOICE:, |_ RISK:, and |_ PLAN:. Do not use free-form paragraphs. At this level, ALWAYS include a full <think> block before any tool call or final answer.]"
                elif "THINKING BEHAVIOR - HIGH" in sys_content:
                    req = "\n\n[FORMATTING REQUIREMENT: Inside your <think> block, use the exact prefixes: |_ UNDERSTAND:, |_ OPTIONS:, |_ CHOICE:, and |_ PLAN:. Do not use free-form paragraphs.]"
                elif "THINKING BEHAVIOR - MEDIUM" in sys_content:
                    req = "\n\n[FORMATTING REQUIREMENT: Inside your <think> block, use the exact prefixes: |_ UNDERSTAND: and |_ PLAN:. Keep it concise — one short line per prefix where possible.]"

                if req and messages and messages[-1]["role"] == "user":
                    messages[-1]["content"] += req

            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 100000,
                "stream": True,
                "timeout": None,
            }

            if (
                hasattr(self, "provider_module")
                and self.provider_module
                and hasattr(self.provider_module, "modify_chat_kwargs")
            ):
                self.provider_module.modify_chat_kwargs(kwargs, reasoning_budget)
            if openai_tools:
                kwargs["tools"] = openai_tools
            response = self.client.chat.completions.create(**kwargs)

            if not kwargs.get("stream", True):
                if hasattr(response, "choices") and response.choices:
                    msg = response.choices[0].message
                    if hasattr(msg, "content") and msg.content:
                        yield msg.content, "", None
                    if getattr(msg, "tool_calls", None):
                        final_calls = []
                        for tc in msg.tool_calls:
                            final_calls.append(
                                {
                                    "id": getattr(tc, "id", "") or "",
                                    "type": "function",
                                    "function": {
                                        "name": getattr(tc.function, "name", "") or "",
                                        "arguments": getattr(
                                            tc.function, "arguments", ""
                                        )
                                        or "",
                                    },
                                }
                            )
                        yield "", "", final_calls
                return

            for chunk in response:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in active_tool_calls:
                            active_tool_calls[idx] = {
                                "id": tc.id or f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or "",
                                },
                            }

                            if tc.function.name:
                                yield (
                                    "",
                                    f"- Running tool: {tc.function.name}...\n",
                                    None,
                                )
                        else:
                            if tc.function.name:
                                if (
                                    tc.function.name
                                    not in active_tool_calls[idx]["function"]["name"]
                                ):
                                    active_tool_calls[idx]["function"]["name"] += (
                                        tc.function.name
                                    )
                            if tc.function.arguments:
                                active_tool_calls[idx]["function"]["arguments"] += (
                                    tc.function.arguments
                                )

                if delta.content:
                    full_content_str += delta.content
                    content_buffer += delta.content

                    while content_buffer:
                        if not in_thinking:
                            idx1 = content_buffer.find("<think>")
                            idx2 = content_buffer.find("<thinking>")
                            idx3 = content_buffer.find("<|channel>thought")
                            idx4 = content_buffer.find("<|think|>")

                            idx = idx1
                            tag_len = 7
                            if idx2 != -1 and (idx == -1 or idx2 < idx):
                                idx = idx2
                                tag_len = 10
                            if idx3 != -1 and (idx == -1 or idx3 < idx):
                                idx = idx3
                                tag_len = 18
                            if idx4 != -1 and (idx == -1 or idx4 < idx):
                                idx = idx4
                                tag_len = 9

                            if idx != -1:
                                if idx > 0:
                                    yield content_buffer[:idx], "", None
                                in_thinking = True
                                content_buffer = content_buffer[idx + tag_len :]
                            else:
                                if "<" in content_buffer:
                                    last_lt = content_buffer.rfind("<")
                                    if len(content_buffer) - last_lt > 25:
                                        yield content_buffer, "", None
                                        content_buffer = ""
                                    else:
                                        yield content_buffer[:last_lt], "", None
                                        content_buffer = content_buffer[last_lt:]
                                        break
                                else:
                                    yield content_buffer, "", None
                                    content_buffer = ""
                        else:
                            idx1 = content_buffer.find("</think>")
                            idx2 = content_buffer.find("</thinking>")
                            idx3 = content_buffer.find("<channel|>")
                            idx4 = content_buffer.find("</|think|>")
                            idx5 = content_buffer.find("<|/think|>")

                            idx = idx1
                            tag_len = 8
                            if idx2 != -1 and (idx == -1 or idx2 < idx):
                                idx = idx2
                                tag_len = 11
                            if idx3 != -1 and (idx == -1 or idx3 < idx):
                                idx = idx3
                                tag_len = 10
                            if idx4 != -1 and (idx == -1 or idx4 < idx):
                                idx = idx4
                                tag_len = 10
                            if idx5 != -1 and (idx == -1 or idx5 < idx):
                                idx = idx5
                                tag_len = 10

                            if idx != -1:
                                if idx > 0:
                                    yield "", content_buffer[:idx], None
                                in_thinking = False
                                content_buffer = content_buffer[idx + tag_len :]
                            else:
                                if "<" in content_buffer:
                                    last_lt = content_buffer.rfind("<")
                                    if len(content_buffer) - last_lt > 25:
                                        yield "", content_buffer, None
                                        content_buffer = ""
                                    else:
                                        yield "", content_buffer[:last_lt], None
                                        content_buffer = content_buffer[last_lt:]
                                        break
                                else:
                                    yield "", content_buffer, None
                                    content_buffer = ""

            if content_buffer:
                if in_thinking:
                    yield "", content_buffer, None
                else:
                    yield content_buffer, "", None

            if not active_tool_calls and full_content_str.strip():
                import re

                json_blocks = re.findall(
                    r"```json\s*(.*?)\s*```", full_content_str, re.DOTALL
                )

                if not json_blocks:
                    json_blocks = re.findall(
                        r"<json>\s*(.*?)\s*</json>", full_content_str, re.DOTALL
                    )
                    if not json_blocks and "<json>" in full_content_str:
                        parts = full_content_str.split("<json>")
                        if len(parts) > 1:
                            json_blocks = [parts[-1].strip()]

                if not json_blocks and "```json" in full_content_str:
                    parts = full_content_str.split("```json")
                    if len(parts) > 1:
                        json_blocks = [parts[-1].strip()]

                if not json_blocks:
                    idx = full_content_str.find("{")
                    if idx != -1:
                        raw = full_content_str[idx:].strip()
                        raw = re.sub(
                            r"</?tool_call>\s*$", "", raw, flags=re.DOTALL
                        ).strip()
                        raw = re.sub(r"</json>\s*$", "", raw, flags=re.DOTALL).strip()
                        if raw.startswith("{") and "name" in raw:
                            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
                            if json_match:
                                raw = json_match.group(0)
                            json_blocks = [raw]

                cleaned_blocks = []
                for b in json_blocks:
                    b = re.sub(r"</json>\s*$", "", b).strip()
                    cleaned_blocks.append(b)
                json_blocks = cleaned_blocks

                if not json_blocks:
                    md_blocks = re.findall(
                        r"```(?:html|css|js|javascript|python|py|cpp|c|java|go|rs|rust|sh|bash)(.*?)```",
                        full_content_str,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if not md_blocks and "```" in full_content_str:
                        parts = re.split(
                            r"```(?:html|css|js|javascript|python|py|cpp|c|java|go|rs|rust|sh|bash)?",
                            full_content_str,
                            flags=re.IGNORECASE,
                        )
                        if len(parts) > 1:
                            md_blocks = [parts[-1]]

                    if md_blocks:
                        code = md_blocks[-1].strip()

                        path = "rescued_file.txt"
                        action_match = re.search(
                            r"NEXT_ACTION:\s*(.*?)(?:\n|$)", full_content_str
                        )
                        if action_match:
                            file_match = re.search(
                                r"([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9]+)",
                                action_match.group(1),
                            )
                            if file_match:
                                path = file_match.group(1)

                        idx = len(active_tool_calls)
                        active_tool_calls[idx] = {
                            "id": f"call_md_rescued_{idx}",
                            "type": "function",
                            "function": {
                                "name": "create_file",
                                "arguments": json.dumps(
                                    {"path": path, "content": code}
                                ),
                            },
                        }

                for block in json_blocks:
                    try:
                        parsed = json.loads(block)
                        if (
                            isinstance(parsed, dict)
                            and "name" in parsed
                            and "arguments" in parsed
                        ):
                            idx = len(active_tool_calls)
                            active_tool_calls[idx] = {
                                "id": f"call_fallback_{idx}",
                                "type": "function",
                                "function": {
                                    "name": parsed["name"],
                                    "arguments": json.dumps(parsed["arguments"])
                                    if isinstance(parsed["arguments"], dict)
                                    else parsed["arguments"],
                                },
                            }
                    except Exception as e:
                        import re

                        name_m = re.search(
                            r'["\']?name["\']?\s*:\s*["\']([^"\']+)["\']', block
                        )
                        path_m = re.search(
                            r'["\']?(?:path|TargetFile|file_path|file)["\']?\s*:\s*["\']([^"\']+)["\']',
                            block,
                        )
                        code_m = re.search(
                            r'["\']?(?:code|content|command|file_content|CodeContent)["\']?\s*:\s*[\"\'\`]+(.*)',
                            block,
                            re.DOTALL,
                        )

                        rescued = False
                        if name_m and path_m and code_m:
                            name = name_m.group(1)
                            path = path_m.group(1)
                            code_raw = code_m.group(1)

                            code_raw = re.sub(r"[\"\'\`\n\}]+$", "", code_raw)

                            code = (
                                code_raw.replace("\\n", "\n")
                                .replace('\\"', '"')
                                .replace("\\\\", "\\")
                            )

                            if name in ["write_file", "create_file"]:
                                idx = len(active_tool_calls)
                                active_tool_calls[idx] = {
                                    "id": f"call_rescued_{idx}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(
                                            {"path": path, "content": code}
                                        ),
                                    },
                                }
                                rescued = True

                        if not rescued:
                            idx = len(active_tool_calls)
                            active_tool_calls[idx] = {
                                "id": f"call_fallback_err_{idx}",
                                "type": "function",
                                "function": {
                                    "name": "syntax_error",
                                    "arguments": json.dumps(
                                        {
                                            "raw_broken_json": block[:500] + "...",
                                            "error": str(e),
                                        }
                                    ),
                                },
                            }

            if active_tool_calls:
                final_calls = []
                for idx, tc_data in active_tool_calls.items():
                    args_str = tc_data["function"]["arguments"]
                    try:
                        tc_data["function"]["arguments"] = json.loads(args_str)
                    except Exception:
                        pass
                    final_calls.append(tc_data)

                yield "", "", final_calls

        except Exception as e:
            err_str = str(e).lower()
            if (
                "peer closed connection" in err_str
                or "incomplete chunked read" in err_str
                or "readerror" in err_str
            ):
                if active_tool_calls:
                    final_calls = []
                    for idx, tc_data in active_tool_calls.items():
                        args_str = tc_data["function"]["arguments"]
                        try:
                            tc_data["function"]["arguments"] = json.loads(args_str)
                        except Exception:
                            pass
                        final_calls.append(tc_data)
                    yield "", "", final_calls
                return
            yield f"\n[API Error: {str(e)}]\n", "", None
