from openai import OpenAI


PROVIDER_ID = "github"
DISPLAY_NAME = "GitHub Models"
BASE_URL = "https://models.github.ai/inference"

def match_url(url: str) -> bool:
    return 'models.github.ai' in url or 'models.inference.ai.azure.com' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=api_key,
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
