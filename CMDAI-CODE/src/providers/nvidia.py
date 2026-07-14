from openai import OpenAI


PROVIDER_ID = "nvidia"
DISPLAY_NAME = "Nvidia NIM"
BASE_URL = "https://integrate.api.nvidia.com/v1"

def match_url(url: str) -> bool:
    return 'nvidia' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    if reasoning_budget > 0:
        kwargs['extra_body'] = { 'chat_template_kwargs': { 'enable_thinking': True }, 'reasoning_budget': reasoning_budget }
    else:
        kwargs['extra_body'] = { 'chat_template_kwargs': { 'enable_thinking': False } }
