import json
import re
from typing import List, Dict, Any, Generator, Tuple
from llama_cpp import Llama
import llama_cpp
import ctypes

def _mute_log(level, text, user_data):
    pass

try:
    _mute_log_cb = llama_cpp.llama_log_callback(_mute_log)
    llama_cpp.llama_log_set(_mute_log_cb, ctypes.c_void_p())
except:
    pass

# --- Definitive fix for "can only concatenate str (not 'NoneType') to str" ---
# Monkey-patch Jinja2's SandboxedEnvironment and Environment getattr/getitem so that ANY
# attribute or dictionary key access returning None is converted to "" before string operations.
try:
    import jinja2.sandbox
    import jinja2.environment

    _orig_sandbox_getattr = jinja2.sandbox.SandboxedEnvironment.getattr
    def _patched_sandbox_getattr(self, obj, attribute):
        res = _orig_sandbox_getattr(self, obj, attribute)
        return "" if res is None else res
    jinja2.sandbox.SandboxedEnvironment.getattr = _patched_sandbox_getattr

    _orig_sandbox_getitem = jinja2.sandbox.SandboxedEnvironment.getitem
    def _patched_sandbox_getitem(self, obj, argument):
        res = _orig_sandbox_getitem(self, obj, argument)
        return "" if res is None else res
    jinja2.sandbox.SandboxedEnvironment.getitem = _patched_sandbox_getitem

    _orig_env_getattr = jinja2.environment.Environment.getattr
    def _patched_env_getattr(self, obj, attribute):
        res = _orig_env_getattr(self, obj, attribute)
        return "" if res is None else res
    jinja2.environment.Environment.getattr = _patched_env_getattr

    _orig_env_getitem = jinja2.environment.Environment.getitem
    def _patched_env_getitem(self, obj, argument):
        res = _orig_env_getitem(self, obj, argument)
        return "" if res is None else res
    jinja2.environment.Environment.getitem = _patched_env_getitem
except Exception:
    pass

def _sanitize_messages_for_llama(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure zero None values exist anywhere in the message dicts before Jinja2 rendering."""
    clean_messages = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        clean_msg = {}
        clean_msg["role"] = str(msg.get("role") or "user")
        clean_msg["content"] = str(msg.get("content") or "")

        # Always include these fields as strings if present, even if None
        for key in ("name", "tool_call_id"):
            if key in msg:
                clean_msg[key] = str(msg[key] or "")

        if "thinking" in msg:
            clean_msg["thinking"] = str(msg["thinking"] or "")

        if "tool_calls" in msg and msg["tool_calls"]:
            clean_tcs = []
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                clean_tc = {}
                # Ensure id and type are always strings
                clean_tc["id"] = str(tc.get("id") or f"call_{len(clean_tcs)}")
                clean_tc["type"] = str(tc.get("type") or "function")
                if "index" in tc:
                    clean_tc["index"] = tc["index"]
                if "function" in tc and isinstance(tc["function"], dict):
                    func = {}
                    func["name"] = str(tc["function"].get("name") or "")
                    args = tc["function"].get("arguments")
                    if isinstance(args, dict):
                        func["arguments"] = json.dumps(args)
                    else:
                        func["arguments"] = str(args or "")
                    func["description"] = str(tc["function"].get("description") or "")
                    clean_tc["function"] = func
                else:
                    clean_tc["function"] = {"name": "", "arguments": "", "description": ""}
                clean_tcs.append(clean_tc)
            clean_msg["tool_calls"] = clean_tcs

        # Catch-all: recursively ensure no None values leak through
        clean_msg = _deep_str_none(clean_msg)
        clean_messages.append(clean_msg)
    return clean_messages


def _deep_str_none(obj):
    """Recursively replace any None value with '' in nested dicts/lists."""
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return {str(k): _deep_str_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_str_none(item) for item in obj]
    return obj


def _sanitize_tools_for_llama(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not tools:
        return None
    clean_tools = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        clean_t = dict(t)
        clean_t["type"] = str(clean_t.get("type") or "function")
        if "function" in clean_t and isinstance(clean_t["function"], dict):
            func = dict(clean_t["function"])
            func["name"] = str(func.get("name") or "")
            func["description"] = str(func.get("description") or "")
            params = func.get("parameters")
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}}
            else:
                params = dict(params)
                params["type"] = str(params.get("type") or "object")
                props = params.get("properties")
                if not isinstance(props, dict):
                    params["properties"] = {}
                else:
                    clean_props = {}
                    for prop_k, prop_v in props.items():
                        if isinstance(prop_v, dict):
                            clean_prop_v = dict(prop_v)
                            clean_prop_v["type"] = str(clean_prop_v.get("type") or "string")
                            clean_prop_v["description"] = str(clean_prop_v.get("description") or "")
                            clean_props[str(prop_k)] = clean_prop_v
                        else:
                            clean_props[str(prop_k)] = {"type": "string", "description": ""}
                    params["properties"] = clean_props
            func["parameters"] = params
            clean_t["function"] = func
        clean_tools.append(clean_t)
    return _deep_str_none(clean_tools)


class LlamaModel:
    def __init__(self, model_path: str, n_ctx: int = 0, n_gpu_layers: int = 8):
        import os
        self.model_path = os.path.abspath(model_path)
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            from llama_cpp import Llama
            try:
                self._llm = Llama(
                    model_path=self.model_path,
                    n_ctx=self.n_ctx,
                    n_gpu_layers=self.n_gpu_layers,
                    verbose=False
                )
            except ValueError as e:
                if "Failed to create llama_context" in str(e) or "Failed to load model" in str(e):
                    if self.n_ctx > 4096:
                        self.n_ctx = max(self.n_ctx // 2, 4096)
                        self._llm = Llama(
                            model_path=self.model_path,
                            n_ctx=self.n_ctx,
                            n_gpu_layers=self.n_gpu_layers,
                            verbose=False
                        )
                    else:
                        raise e
                else:
                    raise e
        return self._llm

    def get_context_limit(self) -> int:
        if self._llm is not None:
            return self.llm.n_ctx()
        return 16384 if self.n_ctx == 0 else self.n_ctx

    def stream_chat(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]] = None, grammar_path: str = None, **kwargs) -> Generator[Tuple[str, str, Dict], None, None]:
        """
        Streams the response from the model.
        Yields tuples of (content_chunk, thinking_chunk, tool_calls_dict)
        """
        grammar = llama_cpp.LlamaGrammar.from_file(grammar_path) if grammar_path else None
        clean_msgs = _sanitize_messages_for_llama(messages)
        clean_tools = _sanitize_tools_for_llama(tools)
        response = self.llm.create_chat_completion(
            messages=clean_msgs,
            tools=clean_tools,
            stream=True,
            temperature=0.3,
            repeat_penalty=1.15,
            grammar=grammar
        )
        
        in_thinking = False
        content_buffer = ""
        
        tool_calls = None
        for chunk in response:
            delta = chunk["choices"][0]["delta"]
            
            if "tool_calls" in delta:
                if tool_calls is None:
                    tool_calls = delta["tool_calls"]
                else:
                    for tc in delta["tool_calls"]:
                        if "function" in tc and "arguments" in tc["function"]:
                            idx = tc["index"]
                            if idx < len(tool_calls):
                                if "arguments" not in tool_calls[idx]["function"] or tool_calls[idx]["function"]["arguments"] is None:
                                    tool_calls[idx]["function"]["arguments"] = ""
                                tool_calls[idx]["function"]["arguments"] += (tc["function"]["arguments"] or "")
            
            if "content" in delta and delta["content"]:
                content_buffer += delta["content"]
                
                while content_buffer:
                    if not in_thinking:
                        idx1 = content_buffer.find("<think>")
                        idx2 = content_buffer.find("<thinking>")
                        idx3 = content_buffer.find("<|think|>")
                        
                        idx = idx1
                        tag_len = 7
                        if idx2 != -1 and (idx == -1 or idx2 < idx):
                            idx = idx2
                            tag_len = 10
                        if idx3 != -1 and (idx == -1 or idx3 < idx):
                            idx = idx3
                            tag_len = 9
                        
                        if idx != -1:
                            if idx > 0:
                                yield content_buffer[:idx], "", None
                            in_thinking = True
                            content_buffer = content_buffer[idx+tag_len:]
                        else:
                                                                        
                            if "<" in content_buffer:
                                last_lt = content_buffer.rfind("<")
                                yield content_buffer[:last_lt], "", None
                                content_buffer = content_buffer[last_lt:]
                                break
                            else:
                                yield content_buffer, "", None
                                content_buffer = ""
                    else:
                        idx1 = content_buffer.find("</think>")
                        idx2 = content_buffer.find("</thinking>")
                        idx3 = content_buffer.find("</|think|>")
                        idx4 = content_buffer.find("<|/think|>")
                        
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
                        
                        if idx != -1:
                            if idx > 0:
                                yield "", content_buffer[:idx], None
                            in_thinking = False
                            content_buffer = content_buffer[idx+tag_len:]
                        else:
                            if "<" in content_buffer:
                                last_lt = content_buffer.rfind("<")
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
                    
        if tool_calls:
            yield "", "", tool_calls
