from __future__ import annotations

import array
import io
import logging
import math
import subprocess
import threading
from collections.abc import Callable
from typing import cast

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2
CHUNK_MS = 40
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000

log = logging.getLogger(__name__)


def pcm_duration_ms(byte_count: int) -> int:
    return byte_count * 1000 // (SAMPLE_RATE * BYTES_PER_SAMPLE)


def rms(chunk: bytes) -> float:
    samples = array.array("h", chunk[: len(chunk) - len(chunk) % BYTES_PER_SAMPLE])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Recorder:
    def __init__(self, on_chunk: Callable[[bytes], None]) -> None:
        self._on_chunk = on_chunk
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._process = subprocess.Popen(
            [
                "parec",
                "--format=s16le",
                f"--rate={SAMPLE_RATE}",
                "--channels=1",
                f"--latency-msec={CHUNK_MS}",
                "--client-name=live-dictate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stream = cast("io.BufferedReader", self._process.stdout)
        self._thread = threading.Thread(
            target=self._pump, args=(stream,), name="recorder", daemon=True
        )
        self._thread.start()

    def _pump(self, stream: io.BufferedReader) -> None:
        while True:
            chunk = stream.read1(CHUNK_BYTES)
            if not chunk:
                break
            self._on_chunk(chunk)

    def stop(self) -> None:
        """Stops capture and returns once every captured chunk has been delivered."""
        if not self._process:
            return
        self._process.terminate()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log.warning("parec did not exit, killing it")
            self._process.kill()
        self._process = None
        self._thread = None
