import os

from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

load_dotenv()

OMNIROUTE_BASE_URL = os.getenv(
    "OMNIROUTE_BASE_URL",
    "http://127.0.0.1:20128/v1",
)

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", "not-configured")

ROUTES = {
    "fast": os.getenv("MODEL_FAST", "auto/best-fast"),
    "reasoning": os.getenv("MODEL_REASONING", "auto/best-reasoning"),
    "free": os.getenv("MODEL_FREE", "auto/best-free"),
    "coding": os.getenv("MODEL_CODING", "auto/best-coding"),
    "vision": os.getenv("MODEL_VISION", "auto/best-vision"),
}


def cloud_model(route: str = "free") -> OpenAIChatModel:
    model_name = ROUTES[route]

    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=OMNIROUTE_BASE_URL,
            api_key=OMNIROUTE_API_KEY,
        ),
    )
