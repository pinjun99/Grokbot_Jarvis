"""Voice wake-up: say your wake phrase anywhere near the Mac.

These are only the ears. Speech is turned into text on the Mac itself
(Whisper, running offline). When it hears the wake phrase it either opens a live
voice chat in the Grok Bot app (wake_action "voice_chat", the default: Grok then
listens and answers in its own voice), or hands the request to a Grok Bot routine's
webhook (wake_action "webhook"). Grok Bot does all the thinking either way.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import time
import urllib.request
from collections.abc import Callable, Iterable
from difflib import SequenceMatcher

SAMPLE_RATE = 16000
FRAME_SECONDS = 0.03

PROMPT_TO_GROK = (
    "The user just said this out loud to their Mac: \"{text}\". Do it using the Jarvis connector. "
    "Keep them updated out loud with Jarvis's speak tool, briefly, since they may not be looking at the screen."
)


def _normalise(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def match_wake(text: str, phrases: Iterable[str], threshold: float = 0.75) -> tuple[bool, str]:
    """Does `text` start with one of the wake phrases? Returns (matched, the rest of what was said).

    Fuzzy, because speech-to-text often hears "hey jarvis" as "hey, jervis" or "a jarvis".
    """
    words = _normalise(text)
    for phrase in phrases:
        target = _normalise(phrase)
        if not target:
            continue
        n = len(target)
        # allow one stray word before the phrase ("oh hey jarvis", "uh hey jarvis")
        for start in (0, 1):
            heard = words[start:start + n]
            if len(heard) == n and SequenceMatcher(None, " ".join(heard), " ".join(target)).ratio() >= threshold:
                rest_words = text.split()[start + n:]
                return True, " ".join(rest_words).strip(" ,.!?")
    return False, ""


class Segmenter:
    """Cuts a stream of microphone audio into separate utterances, using loudness.

    Feed it frames (lists/arrays of float samples); it returns a finished
    utterance when someone stops talking, else None.
    """

    def __init__(self, silence_seconds: float = 0.8, max_seconds: float = 15.0,
                 min_seconds: float = 0.3, sensitivity: float = 3.0):
        self.silence_frames = int(silence_seconds / FRAME_SECONDS)
        self.max_frames = int(max_seconds / FRAME_SECONDS)
        self.min_frames = int(min_seconds / FRAME_SECONDS)
        self.sensitivity = sensitivity
        self.noise_floor = 0.003
        self._frames: list = []
        self._quiet = 0
        self._loud = 0

    @staticmethod
    def _rms(frame) -> float:
        if hasattr(frame, "dtype"):  # numpy array from the microphone
            return float((frame.astype("float64") ** 2).mean() ** 0.5) if len(frame) else 0.0
        n = len(frame) or 1
        return (sum(float(x) * float(x) for x in frame) / n) ** 0.5

    def feed(self, frame) -> list | None:
        level = self._rms(frame)
        speaking = level > self.noise_floor * self.sensitivity
        if not self._frames:
            if not speaking:
                # slowly track background noise while nobody is talking
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * max(level, 0.0005)
                return None
            self._loud = 0
        self._frames.append(frame)
        if speaking:
            self._quiet = 0
            self._loud += 1
        else:
            self._quiet += 1
        if self._quiet >= self.silence_frames or len(self._frames) >= self.max_frames:
            frames, loud = self._frames, self._loud
            self._frames, self._quiet, self._loud = [], 0, 0
            return frames if loud >= self.min_frames else None
        return None


def send_to_grok(url: str, text: str, headers: dict | None = None, timeout: float = 15) -> int:
    body = json.dumps({
        "source": "jarvis-voice",
        "text": text,
        "message": PROMPT_TO_GROK.format(text=text),
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def chime(name: str = "Tink") -> None:
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


class Listener:
    """Always-on listening loop. Runs until stopped (Ctrl+C)."""

    def __init__(self, cfg: dict, log: Callable[[str], None], is_paused: Callable[[], bool], grok_app=None):
        self.cfg = cfg
        self.log = log
        self.is_paused = is_paused
        self.phrases = cfg["wake_phrases"]
        self.voice_chat = cfg.get("wake_action", "voice_chat") == "voice_chat"
        if grok_app is None and self.voice_chat:
            from .grokbot import GrokBotApp

            grok_app = GrokBotApp()
        self.grok_app = grok_app
        self._model = None

    def transcribe(self, audio) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.cfg["speech_model"], device="cpu", compute_type="int8")
        # vad_filter drops non-speech, so background noise isn't "heard" as words
        segments, _info = self._model.transcribe(
            audio, language=self.cfg.get("speech_language") or None, beam_size=1, vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def open_voice_chat(self) -> None:
        if self.is_paused():
            subprocess.Popen(["say", "Jarvis is paused."])
            return
        try:
            started = self.grok_app.start_voice_chat(self.cfg.get("grok_bot_name", ""))
        except Exception as e:
            self.log(f"VOICE could not open Grok Bot voice chat: {e}")
            subprocess.Popen(["say", "Sorry, I couldn't open Grok Bot."])
            return
        if started:
            chime("Pop")
            self.log("VOICE -> opened Grok Bot voice chat")

    def handle(self, text: str, awaiting_command: bool) -> bool:
        """Returns True if the next utterance should be treated as the command."""
        matched, rest = match_wake(text, self.phrases)
        if self.voice_chat:
            # Grok Bot hears the request itself once the voice chat is open
            if matched:
                self.open_voice_chat()
            return False
        if matched:
            command = rest
        elif awaiting_command:
            command = text
        else:
            return False
        if not command:
            chime("Tink")
            return True  # just the wake phrase: listen for the request next
        if self.is_paused():
            subprocess.Popen(["say", "Jarvis is paused."])
            return False
        chime("Pop")
        self.log(f"VOICE -> Grok Bot: {command}")
        try:
            send_to_grok(self.cfg["voice_webhook_url"], command, self.cfg.get("voice_webhook_headers"))
        except Exception as e:
            self.log(f"VOICE send failed: {e}")
            subprocess.Popen(["say", "Sorry, I couldn't reach Grok."])
        return False

    def run(self, on_ready: Callable[[], None] = lambda: None) -> None:
        import numpy as np
        import sounddevice as sd

        if not self.voice_chat and not self.cfg.get("voice_webhook_url"):
            raise RuntimeError("The voice webhook isn't set up yet. Run: jarvis voice webhook")
        print("Loading the speech model (the first time this downloads it, ~500 MB)...")
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

        segmenter = Segmenter()
        awaiting_until = 0.0
        frame_len = int(SAMPLE_RATE * FRAME_SECONDS)
        # The microphone fills a queue in the background, so nothing you say is lost
        # while the previous sentence is still being turned into text.
        frames: queue.Queue = queue.Queue()

        def on_audio(indata, _frames, _time, _status) -> None:
            frames.put(indata[:, 0].copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=frame_len,
                            callback=on_audio):
            on_ready()
            while True:
                utterance = segmenter.feed(frames.get())
                if utterance is None:
                    continue
                if self.voice_chat and self.grok_app.in_voice_chat():
                    continue  # you're talking to Grok Bot, not to Jarvis
                text = self.transcribe(np.concatenate(utterance))
                if not text:
                    continue
                awaiting = time.monotonic() < awaiting_until
                if self.handle(text, awaiting):
                    awaiting_until = time.monotonic() + 8
                else:
                    awaiting_until = 0.0
