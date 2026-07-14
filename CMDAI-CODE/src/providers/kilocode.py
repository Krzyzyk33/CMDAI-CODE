from openai import OpenAI
import httpx

PROVIDER_ID = "kilocode"
DISPLAY_NAME = "Kilo Code"
BASE_URL = "https://api.kilo.ai/api/gateway"

def match_url(url: str) -> bool:
    return 'kilo' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://api.kilo.ai/api/gateway",
        api_key=api_key,
        http_client=httpx.Client(verify=False)
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
