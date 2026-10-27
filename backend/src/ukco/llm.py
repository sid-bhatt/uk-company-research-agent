"""LLM API clients built from settings (keys are read here and nowhere else)."""

import anthropic
import openai

from ukco.config import Settings


def openai_client(settings: Settings) -> openai.AsyncOpenAI:
    """Any OpenAI-compatible Chat Completions API: Gemini, Groq, OpenRouter, Ollama, …"""
    assert settings.llm_api_key is not None
    return openai.AsyncOpenAI(
        api_key=settings.llm_api_key.get_secret_value().strip(),
        base_url=settings.llm_base_url,
        max_retries=3,  # free tiers rate-limit (429) readily; the SDK backs off
    )


def anthropic_client(settings: Settings) -> anthropic.AsyncAnthropic:
    assert settings.anthropic_api_key is not None
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value().strip())
