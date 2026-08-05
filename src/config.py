import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY")
    OPEN_ROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    MODEL = "openai/gpt-4o-mini"

    # "FULL"   -> every existing order/item/seller/payment id becomes evidence.
    # "CAUSAL" -> only the ids implicated by the root cause (drops seller ids
    #             when the seller is not at fault, item ids on platform-fault
    #             cases). A/B switch for the evidence dimension.
    EVIDENCE_MODE = "FULL"
