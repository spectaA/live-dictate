from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from live_dictate.audio import CHUNK_BYTES
from live_dictate.config import Config
from live_dictate.transcriber import (
    LiveTranscriber,
    TranscriptAssembler,
    TranscriptionError,
    build_session_update,
)


def test_session_update_uses_languages_and_disables_turn_detection() -> None:
    config = Config(languages=["fr"], keywords=["Kubernetes"], transcription_prompt="Dev talk")
    payload = build_session_update(config)
    audio_input = payload["session"]["audio"]["input"]
    assert payload["session"]["type"] == "transcription"
    assert audio_input["turn_detection"] is None
    assert audio_input["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio_input["transcription"]["languages"] == ["fr"]
    assert "language" not in audio_input["transcription"]
    assert audio_input["transcription"]["keywords"] == ["Kubernetes"]
    assert audio_input["transcription"]["prompt"] == "Dev talk"


def test_session_update_omits_empty_optional_fields() -> None:
    session = build_session_update(Config(languages=[]))
    transcription = session["session"]["audio"]["input"]["transcription"]
    assert "prompt" not in transcription
    assert "keywords" not in transcription
    assert "languages" not in transcription


def test_assembler_prefers_completed_text_and_keeps_item_order() -> None:
    assembler = TranscriptAssembler()
    assembler.add_delta("a", "Bonjour ")
    assembler.add_delta("a", "tout")
    assembler.add_committed("b")
    assert assembler.text() == "Bonjour tout"
    assert not assembler.is_complete
    assembler.complete("b", "second")
    assembler.complete("a", "Bonjour tout le monde")
    assert assembler.text() == "Bonjour tout le monde second"
    assert assembler.is_complete


async def _fake_realtime(events_after_commit: list[dict], received: list[dict]):
    async def handler(ws):
        async for message in ws:
            event = json.loads(message)
            received.append(event)
            if event["type"] == "input_audio_buffer.append" and len(received) == 2:
                await ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.input_audio_transcription.delta",
                            "item_id": "i1",
                            "delta": "euh bonjour",
                        }
                    )
                )
            if event["type"] == "input_audio_buffer.commit":
                for reply in events_after_commit:
                    await ws.send(json.dumps(reply))

    return await serve(handler, "127.0.0.1", 0)


async def _feed(queue: asyncio.Queue, chunks: int) -> None:
    for _ in range(chunks):
        await queue.put(b"\x00" * CHUNK_BYTES)
    await queue.put(None)


async def test_run_streams_audio_and_returns_final_transcript() -> None:
    received: list[dict] = []
    server = await _fake_realtime(
        [
            {"type": "input_audio_buffer.committed", "item_id": "i1"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "i1",
                "transcript": "euh bonjour à tous",
            },
        ],
        received,
    )
    port = server.sockets[0].getsockname()[1]
    partials: list[str] = []
    transcriber = LiveTranscriber("key", Config(), partials.append, url=f"ws://127.0.0.1:{port}")
    queue: asyncio.Queue = asyncio.Queue()
    feeder = asyncio.create_task(_feed(queue, 20))
    try:
        result = await asyncio.wait_for(transcriber.run(queue), 5)
    finally:
        await feeder
        server.close()

    assert result == "euh bonjour à tous"
    assert received[0]["type"] == "session.update"
    assert received[-1]["type"] == "input_audio_buffer.commit"
    assert sum(e["type"] == "input_audio_buffer.append" for e in received) == 20
    assert partials[0] == "euh bonjour"


async def test_run_skips_commit_when_audio_is_too_short() -> None:
    received: list[dict] = []
    server = await _fake_realtime([], received)
    port = server.sockets[0].getsockname()[1]
    transcriber = LiveTranscriber("key", Config(), lambda _: None, url=f"ws://127.0.0.1:{port}")
    queue: asyncio.Queue = asyncio.Queue()
    await _feed(queue, 2)
    try:
        result = await asyncio.wait_for(transcriber.run(queue), 5)
    finally:
        server.close()

    assert result == ""
    assert all(e["type"] != "input_audio_buffer.commit" for e in received)


async def test_run_raises_on_error_event() -> None:
    received: list[dict] = []
    server = await _fake_realtime(
        [{"type": "error", "error": {"message": "invalid api key"}}], received
    )
    port = server.sockets[0].getsockname()[1]
    transcriber = LiveTranscriber("key", Config(), lambda _: None, url=f"ws://127.0.0.1:{port}")
    queue: asyncio.Queue = asyncio.Queue()
    await _feed(queue, 20)
    try:
        with pytest.raises(TranscriptionError, match="invalid api key"):
            await asyncio.wait_for(transcriber.run(queue), 5)
    finally:
        server.close()
