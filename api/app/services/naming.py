"""Short titles for baskets saved without a name, the way chat apps title a conversation."""

import logging

from app.llm import LLM, LLMError
from app.models import BasketPosition

log = logging.getLogger(__name__)

MAX_NAME_LENGTH = 200
# What an unnamed basket is called until it's titled.
PLACEHOLDER_NAME = "New basket"
# Enough markets to show what the basket is about without a long prompt.
MAX_MARKETS_SHOWN = 12
# The model is asked for 2-6 words; anything far past that is a sentence, not a title.
MAX_MODEL_NAME_LENGTH = 60

_SYSTEM = """You name prediction market baskets. Given what the basket is about and the \
markets in it, reply with a short title: 2-6 words, Title Case, no quotes, no trailing \
punctuation, no words like "Basket" or "Portfolio". Name the theme, not a single market.

Return JSON: {"name": "<title>"}"""

_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def fallback_name(description: str | None, positions: list[BasketPosition]) -> str:
    text = (description or "").strip() or positions[0].market.question.strip() or "Untitled basket"
    return _truncate(text, MAX_NAME_LENGTH)


def suggest_name(namer: LLM | None, description: str | None, positions: list[BasketPosition]) -> str:
    """A model-written title, or the description (else the first market) when the model is unavailable."""
    if namer is None:
        return fallback_name(description, positions)
    markets = "\n".join(
        f"- {p.side.value.upper()}: {p.market.question}" for p in positions[:MAX_MARKETS_SHOWN]
    )
    user = f"About: {description or '(none)'}\nMarkets:\n{markets}"
    try:
        reply = namer.complete_json(_SYSTEM, user, _SCHEMA)
    except LLMError:
        log.warning("Naming a basket failed; using its description", exc_info=True)
        return fallback_name(description, positions)
    name = str(reply.get("name", "")).strip().strip("\"'").rstrip(".").strip()
    if not name or len(name) > MAX_MODEL_NAME_LENGTH:
        return fallback_name(description, positions)
    return name


def unused_name(base: str, taken: set[str]) -> str:
    """Basket names are unique per owner, so a repeat gets " (2)", " (3)", ..."""
    name, n = base, 2
    while name in taken:
        suffix = f" ({n})"
        name = f"{_truncate(base, MAX_NAME_LENGTH - len(suffix))}{suffix}"
        n += 1
    return name
