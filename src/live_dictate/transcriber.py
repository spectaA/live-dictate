from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect

from .audio import SAMPLE_RATE, pcm_duration_ms
from .config import Config

REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
FINAL_TIMEOUT_S = 10.0

log = logging.getLogger(__name__)

# asyncio keeps only weak references to tasks.
_background: set[asyncio.Task[None]] = set()


class TranscriptionError(RuntimeError):
    pass


def build_session_update(config: Config) -> dict[str, Any]:
    transcription: dict[str, Any] = {
        "model": config.transcription_model,
        "delay": config.transcription_delay,
    }
    if config.languages:
        transcription["languages"] = config.languages
    if config.transcription_prompt:
        transcription["prompt"] = config.transcription_prompt
    if config.keywords:
        transcription["keywords"] = config.keywords
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "transcription": transcription,
                    "turn_detection": None,
                }
            },
        },
    }


class TranscriptAssembler:
    """Merges delta and completed events; completion order across items is not guaranteed."""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._deltas: dict[str, str] = {}
        self._completed: dict[str, str] = {}

    def _track(self, item_id: str) -> None:
        if item_id not in self._order:
            self._order.append(item_id)

    def add_delta(self, item_id: str, delta: str) -> None:
        self._track(item_id)
        self._deltas[item_id] = self._deltas.get(item_id, "") + delta

    def add_committed(self, item_id: str) -> None:
        self._track(item_id)

    def complete(self, item_id: str, transcript: str) -> None:
        self._track(item_id)
        self._completed[item_id] = transcript

    @property
    def is_complete(self) -> bool:
        return bool(self._completed) and all(i in self._completed for i in self._order)

    def text(self) -> str:
        parts = (self._completed.get(i, self._deltas.get(i, "")) for i in self._order)
        return " ".join(p.strip() for p in parts if p.strip())


class LiveTranscriber:
    def __init__(
        self,
        api_key: str,
        config: Config,
        on_partial: Callable[[str], None],
        url: str = REALTIME_URL,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._config = config
        self._on_partial = on_partial

    async def run(self, audio: asyncio.Queue[bytes | None]) -> str:
        """Streams `audio` until a None sentinel, then returns the final transcript.

        Returns an empty string, without committing, when less than `min_audio_ms` was captured.
        """
        headers = {"Authorization": f"Bearer {self._api_key}"}
        ws = await connect(self._url, additional_headers=headers, max_size=None)
        receiver: asyncio.Task[None] | None = None
        try:
            await ws.send(json.dumps(build_session_update(self._config)))
            assembler = TranscriptAssembler()
            committed = asyncio.Event()
            receiver = asyncio.create_task(self._receive(ws, assembler, committed))
            sent_bytes = await self._send_audio(ws, audio, receiver)
            if pcm_duration_ms(sent_bytes) < self._config.min_audio_ms:
                return ""
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            committed.set()
            await asyncio.wait_for(asyncio.shield(receiver), FINAL_TIMEOUT_S)
            return assembler.text()
        finally:
            if receiver:
                if receiver.done() and not receiver.cancelled():
                    receiver.exception()
                receiver.cancel()
            # The server takes ~2 s to end TCP after the close handshake; never block on it.
            closing = asyncio.create_task(ws.close())
            _background.add(closing)
            closing.add_done_callback(_background.discard)

    async def _send_audio(
        self, ws: Any, audio: asyncio.Queue[bytes | None], receiver: asyncio.Task[None]
    ) -> int:
        sent = 0
        while True:
            chunk = await audio.get()
            if chunk is None:
                return sent
            if receiver.done():
                receiver.result()
            event = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }
            await ws.send(json.dumps(event))
            sent += len(chunk)

    async def _receive(
        self, ws: Any, assembler: TranscriptAssembler, committed: asyncio.Event
    ) -> None:
        async for message in ws:
            event = json.loads(message)
            kind = event.get("type", "")
            if kind == "error":
                detail = event.get("error", {})
                raise TranscriptionError(detail.get("message") or json.dumps(detail))
            if kind == "conversation.item.input_audio_transcription.delta":
                assembler.add_delta(event["item_id"], event.get("delta", ""))
                self._on_partial(assembler.text())
            elif kind == "input_audio_buffer.committed":
                assembler.add_committed(event["item_id"])
            elif kind == "conversation.item.input_audio_transcription.completed":
                assembler.complete(event["item_id"], event.get("transcript", ""))
                self._on_partial(assembler.text())
            elif kind == "conversation.item.input_audio_transcription.failed":
                detail = event.get("error", {})
                raise TranscriptionError(detail.get("message") or "transcription failed")
            else:
                log.debug("realtime event %s", kind)
            if committed.is_set() and assembler.is_complete:
                return
        if committed.is_set():
            raise TranscriptionError("connection closed before the final transcript")
