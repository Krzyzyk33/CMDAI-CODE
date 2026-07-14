from openai import OpenAI


PROVIDER_ID = "openai"
DISPLAY_NAME = "OpenAI"
BASE_URL = "https://api.openai.com/v1"

def match_url(url: str) -> bool:
    return 'openai' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://api.openai.com/v1",
        api_key=api_key,
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
