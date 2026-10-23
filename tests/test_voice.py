import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jarvis import voice
from jarvis.voice import FRAME_SECONDS, SAMPLE_RATE, Listener, Segmenter, match_wake, send_to_grok


@pytest.mark.parametrize("heard,matched,rest", [
    ("Hey Jarvis, open Safari.", True, "open Safari"),
    ("hey jervis open claude", True, "open claude"),
    ("Uh, hey Jarvis, what's on my screen?", True, "what's on my screen"),
    ("Hey Jarvis.", True, ""),
    ("I told Jarvis about it yesterday", False, ""),
    ("open safari", False, ""),
])
def test_match_wake(heard, matched, rest):
    assert match_wake(heard, ["hey jarvis"]) == (matched, rest)


def test_custom_wake_phrases():
    assert match_wake("Yo Friday, play music", ["hey jarvis", "yo friday"]) == (True, "play music")


def _frames(seconds, amplitude):
    n = int(SAMPLE_RATE * FRAME_SECONDS)
    frame = [amplitude * math.sin(i / 3) for i in range(n)]
    return [frame] * int(seconds / FRAME_SECONDS)


def test_segmenter_cuts_speech_from_silence():
    seg = Segmenter()
    results = [seg.feed(f) for f in _frames(1, 0.0005) + _frames(1.5, 0.2) + _frames(1, 0.0005)]
    utterances = [r for r in results if r is not None]
    assert len(utterances) == 1
    assert len(utterances[0]) >= int(1.5 / FRAME_SECONDS)


def test_segmenter_ignores_short_clicks():
    seg = Segmenter()
    results = [seg.feed(f) for f in _frames(1, 0.0005) + _frames(0.1, 0.3) + _frames(1, 0.0005)]
    assert all(r is None for r in results)


@pytest.fixture
def webhook():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((dict(self.headers), json.loads(body)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received
    server.shutdown()


def test_send_to_grok(webhook):
    url, received = webhook
    assert send_to_grok(url, "open Safari", {"Authorization": "Bearer s3cret"}) == 200
    headers, body = received[0]
    assert headers["Authorization"] == "Bearer s3cret"
    assert body["text"] == "open Safari"
    assert "open Safari" in body["message"] and "Jarvis" in body["message"]


def test_listener_wake_then_command(webhook, monkeypatch):
    url, received = webhook
    monkeypatch.setattr(voice, "chime", lambda *a: None)
    cfg = {"wake_phrases": ["hey jarvis"], "voice_webhook_url": url, "voice_webhook_headers": {}}
    listener = Listener(cfg, log=lambda m: None, is_paused=lambda: False)

    assert listener.handle("Hey Jarvis.", awaiting_command=False) is True   # wait for the request
    assert listener.handle("open my email", awaiting_command=True) is False
    assert listener.handle("random chatter", awaiting_command=False) is False
    assert listener.handle("hey jarvis open notes", awaiting_command=False) is False
    assert [body["text"] for _, body in received] == ["open my email", "open notes"]


def test_wake_phrase_repeated_while_awaiting(webhook, monkeypatch):
    url, received = webhook
    monkeypatch.setattr(voice, "chime", lambda *a: None)
    cfg = {"wake_phrases": ["hey jarvis"], "voice_webhook_url": url, "voice_webhook_headers": {}}
    listener = Listener(cfg, log=lambda m: None, is_paused=lambda: False)
    assert listener.handle("hey jarvis open safari", awaiting_command=True) is False
    assert [body["text"] for _, body in received] == ["open safari"]


def test_segmenter_with_numpy_frames():
    import numpy as np

    seg = Segmenter()
    quiet = [np.full(480, 0.0005, dtype=np.float32)] * 30
    loud = [np.full(480, 0.2, dtype=np.float32)] * 40
    results = [seg.feed(f) for f in quiet + loud + quiet]
    assert sum(r is not None for r in results) == 1
