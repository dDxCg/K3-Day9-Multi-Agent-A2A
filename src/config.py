import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY")
    OPEN_ROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    MODEL = "openai/gpt-4o-mini"
