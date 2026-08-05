import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY")
    MODEL = "openai/gpt-4o-mini"
