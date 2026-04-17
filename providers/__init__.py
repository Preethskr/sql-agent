from config import LLM_PROVIDER
from providers.base import BaseProvider


def get_provider() -> BaseProvider:
    """
    Factory — reads LLM_PROVIDER from config and returns the correct provider.
    Set LLM_PROVIDER in your .env: anthropic | groq | gemini

    Imports are lazy — only the selected provider's dependencies are loaded.
    """
    key = LLM_PROVIDER.lower().strip()

    if key == "anthropic":
        from providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    elif key == "groq":
        from providers.groq_provider import GroqProvider
        return GroqProvider()

    elif key == "gemini":
        from providers.gemini_provider import GeminiProvider
        return GeminiProvider()

    else:
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{LLM_PROVIDER}'. "
            "Supported values: anthropic, groq, gemini"
        )
