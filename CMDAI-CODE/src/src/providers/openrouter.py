from openai import OpenAI


PROVIDER_ID = "openrouter"
DISPLAY_NAME = "OpenRouter"
BASE_URL = "https://openrouter.ai/api/v1"

def match_url(url: str) -> bool:
    return 'openrouter' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
