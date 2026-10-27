from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config
from .prompts import INSTRUCTIONS

RESPONSES_URL = "https://api.openai.com/v1/responses"

log = logging.getLogger(__name__)


class CleanupError(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanupResult:
    text: str
    failed: bool = False
    service_tier: str | None = None
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


def needs_cleanup(transcript: str, min_words: int) -> bool:
    return len(transcript.split()) >= min_words


def build_request(config: Config, transcript: str) -> dict[str, Any]:
    return {
        "model": config.cleanup_model,
        "instructions": INSTRUCTIONS[config.effective_cleanup_style],
        "input": f"<transcript>\n{transcript}\n</transcript>",
        "reasoning": {"effort": "none"},
        "service_tier": config.cleanup_service_tier,
        "store": False,
    }


def extract_output_text(payload: dict[str, Any]) -> str:
    texts = [
        part.get("text", "")
        for item in payload.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    text = "".join(texts).strip()
    if not text:
        raise CleanupError("response contained no output text")
    return text


def parse_result(payload: dict[str, Any]) -> CleanupResult:
    usage = payload.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    return CleanupResult(
        text=extract_output_text(payload),
        service_tier=payload.get("service_tier"),
        input_tokens=int(usage.get("input_tokens", 0)),
        cached_tokens=int(details.get("cached_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


def cleanup_timeout(config: Config, transcript: str) -> float:
    """Long dictations take longer to rewrite, so the timeout grows with the word count."""
    return config.cleanup_timeout_s + config.cleanup_timeout_per_word_s * len(transcript.split())


class Cleaner:
    def __init__(self, api_key: str, config: Config) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=config.cleanup_timeout_s,
        )

    async def clean(self, transcript: str) -> CleanupResult:
        """Falls back to the raw transcript, flagged as failed, if the call fails or times out."""
        try:
            response = await self._client.post(
                RESPONSES_URL,
                json=build_request(self._config, transcript),
                timeout=cleanup_timeout(self._config, transcript),
            )
            response.raise_for_status()
            result = parse_result(response.json())
        except (httpx.HTTPError, CleanupError, ValueError) as error:
            # Timeouts have an empty message.
            log.warning("cleanup failed, pasting raw transcript: %r", error)
            return CleanupResult(transcript, failed=True)
        # A ramp-rate limit silently downgrades the request to the standard tier.
        if self._config.cleanup_service_tier != "default" and result.service_tier == "default":
            log.warning("cleanup served on the default tier instead of fast")
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
