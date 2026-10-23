"""Jarvis's speaking voice: Grok's own voice (xAI text-to-speech), with the Mac's `say` as backup.

This is only the mouth. Grok Bot decides what to say; xAI just turns the words
into Grok's voice. If anything goes wrong (no key, no internet, xAI error) the
Mac's built-in voice says it instead, so nothing is ever left unsaid.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable

from . import config

TTS_URL = "https://api.x.ai/v1/tts"
VOICES_URL = "https://api.x.ai/v1/tts/voices"
BUILT_IN_VOICES = ["eve", "ara", "leo", "rex", "sal"]


def provider(cfg: dict) -> str:
    """'grok' or 'mac'. 'auto' (the default) means Grok's voice once an xAI key is saved."""
    choice = cfg.get("speech_voice_provider", "auto")
    if choice in ("grok", "mac"):
        return choice
    return "grok" if cfg.get("xai_api_key") else "mac"


def _request(url: str, key: str, body: dict | None = None, timeout: float = 30) -> bytes:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "jarvis-mac"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace").strip()
        hint = {401: "xAI didn't accept the API key", 404: "xAI doesn't know that voice",
                429: "too many requests to xAI right now"}.get(e.code, "xAI returned an error")
        raise RuntimeError(f"{hint} (HTTP {e.code}) {detail}".strip()) from None


def fetch_tts(text: str, key: str, voice_id: str = "eve", language: str = "auto") -> bytes:
    """MP3 audio of `text` spoken in Grok's voice."""
    audio = _request(TTS_URL, key, {"text": text, "voice_id": voice_id, "language": language or "auto"})
    if not audio:
        raise RuntimeError("xAI sent back no audio")
    return audio


def list_voices(key: str) -> list[dict]:
    """[{"voice_id": "eve", "name": "Eve", "language": "en"}, ...]"""
    return json.loads(_request(VOICES_URL, key))["voices"]


class Speaker:
    """Says things one at a time in a background thread, so two sentences never talk over each other."""

    def __init__(self, get_cfg: Callable[[], dict] = config.load, log: Callable[[str], None] = config.log,
                 fetch: Callable[..., bytes] = fetch_tts,
                 run: Callable[..., object] | None = None):
        self.get_cfg = get_cfg
        self.log = log
        self.fetch = fetch
        self.run = run or (lambda args: subprocess.run(args))
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def say(self, text: str) -> None:
        """Non-blocking: queue `text` and return straight away."""
        self._queue.put(text)
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._loop, daemon=True)
                self._worker.start()

    def wait(self) -> None:
        """Block until everything queued so far has been said."""
        self._queue.join()

    def _loop(self) -> None:
        while True:
            text = self._queue.get()
            try:
                self.say_now(text)
            except Exception as e:  # never let the speaking thread die
                self.log(f"SPEAK failed: {e}")
            finally:
                self._queue.task_done()

    def say_now(self, text: str) -> str | None:
        """Blocking. Returns None if Grok's voice spoke, else the reason the Mac voice was used.

        Call it from one thread at a time (the queue does that for `say`)."""
        cfg = self.get_cfg()
        reason = None
        if provider(cfg) == "grok":
            reason = self._grok(text, cfg)
            if reason is None:
                return None
            self.log(f"Grok voice unavailable, using the Mac voice instead: {reason}")
        else:
            reason = "the Mac voice is selected"
        self.run(["say", "-v", cfg.get("voice", "Daniel"), text])
        return reason

    def _grok(self, text: str, cfg: dict) -> str | None:
        key = cfg.get("xai_api_key", "")
        if not key:
            return "no xAI API key saved (run: jarvis voice grok-key)"
        try:
            audio = self.fetch(text, key, cfg.get("grok_voice") or "eve", cfg.get("speech_language") or "auto")
        except Exception as e:
            return str(e)
        fd, path = tempfile.mkstemp(prefix="jarvis-speech-", suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            result = self.run(["afplay", path])
            if getattr(result, "returncode", 0) != 0:
                return f"afplay couldn't play the audio (exit code {result.returncode})"
        except Exception as e:
            return f"couldn't play the audio: {e}"
        finally:
            os.unlink(path)
        return None


_speaker: Speaker | None = None
_speaker_lock = threading.Lock()


def speak(text: str) -> None:
    """Say `text` out loud without waiting. Everything in Jarvis shares one queue."""
    global _speaker
    with _speaker_lock:
        if _speaker is None:
            _speaker = Speaker()
    _speaker.say(text)
