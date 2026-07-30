from openai import OpenAI


PROVIDER_ID = "huggingface"
DISPLAY_NAME = "Hugging Face"
BASE_URL = "https://api-inference.huggingface.co/v1"

def match_url(url: str) -> bool:
    return 'huggingface' in url

def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://api-inference.huggingface.co/v1",
        api_key=api_key,
    )

def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
