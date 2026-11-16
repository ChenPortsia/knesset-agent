"""The chat model, from one setting written as provider:model."""

import os
from typing import Any

from langchain.chat_models import init_chat_model

PROVIDERS = {"google_genai": "GOOGLE_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}  # provider: key


def load_model(name: str | None = None) -> Any:
    """Load a model such as google_genai:gemini-pro-latest (default: KNESSET_MODEL in .env)."""
    name = name or os.environ.get("KNESSET_MODEL", "")
    provider = name.partition(":")[0]
    if ":" not in name or provider not in PROVIDERS:
        raise SystemExit(
            f"KNESSET_MODEL is {name!r}. Write it as provider:model with one of these "
            f"providers: {', '.join(PROVIDERS)}. Example: google_genai:gemini-pro-latest"
        )
    if not os.environ.get(PROVIDERS[provider]):
        raise SystemExit(f"{PROVIDERS[provider]} is not set in .env; {name} needs it.")
    # A stuck call fails after two minutes instead of waiting on the provider's own long retries
    return init_chat_model(name, timeout=120, max_retries=2)
