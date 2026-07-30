from openai import OpenAI


PROVIDER_ID = "zenmux"
DISPLAY_NAME = "ZenMux"
BASE_URL = "https://zenmux.ai/api/v1"


def match_url(url: str) -> bool:
    return "zenmux" in url


def get_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://zenmux.ai/api/v1",
        api_key=api_key,
    )


def modify_chat_kwargs(kwargs: dict, reasoning_budget: int = 0) -> None:
    pass
