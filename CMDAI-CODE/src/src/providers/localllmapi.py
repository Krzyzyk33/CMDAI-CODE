from openai import OpenAI

PROVIDER_ID = "localllmapi"
DISPLAY_NAME = "Local LLM API"
BASE_URL = ""

def match_url(url: str) -> bool:
    return '127.0.0.1' in url or 'localhost' in url or 'localllmapi' in url

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    if "max_tokens" in kwargs:
        del kwargs["max_tokens"]

                                                                                                      
                                                                                                                
    extra_body = kwargs.setdefault("extra_body", {})
    chat_template_kwargs = extra_body.setdefault("chat_template_kwargs", {})
    chat_template_kwargs["enable_thinking"] = False
    kwargs["stream"] = True
    kwargs["timeout"] = 120.0
                                                                                          
    if "messages" in kwargs:
        import copy
        new_msgs = copy.deepcopy(kwargs["messages"])
        for msg in new_msgs:
            if "tool_calls" in msg:
                del msg["tool_calls"]
            if msg.get("role") == "tool":
                msg["role"] = "user"
                msg["content"] = f"[TOOL RESPONSE]\n{msg.get('content')}\n[/TOOL RESPONSE]"
        kwargs["messages"] = new_msgs
