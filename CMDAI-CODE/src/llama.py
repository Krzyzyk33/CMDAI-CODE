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
        response = self.llm.create_chat_completion(
            messages=messages,
            tools=tools,
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
                                if "arguments" not in tool_calls[idx]["function"]:
                                    tool_calls[idx]["function"]["arguments"] = ""
                                tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]
            
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
