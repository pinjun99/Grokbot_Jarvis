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
    cfg = {"wake_phrases": [], "grok_bot_name": "Elon", "wake_action": "webhook", "voice_webhook_url": url,
           "voice_webhook_headers": {}}
    listener = Listener(cfg, log=lambda m: None, is_paused=lambda: False)

    assert listener.handle("Hey Elon.", awaiting_command=False) is True   # wait for the request
    assert listener.handle("open my email", awaiting_command=True) is False
    assert listener.handle("random chatter", awaiting_command=False) is False
    assert listener.handle("hey elon open notes", awaiting_command=False) is False
    assert [body["text"] for _, body in received] == ["open my email", "open notes"]


def test_wake_phrase_repeated_while_awaiting(webhook, monkeypatch):
    url, received = webhook
    monkeypatch.setattr(voice, "chime", lambda *a: None)
    cfg = {"wake_phrases": [], "grok_bot_name": "Elon", "wake_action": "webhook", "voice_webhook_url": url,
           "voice_webhook_headers": {}}
    listener = Listener(cfg, log=lambda m: None, is_paused=lambda: False)
    assert listener.handle("hey elon open safari", awaiting_command=True) is False
    assert [body["text"] for _, body in received] == ["open safari"]


def test_segmenter_with_numpy_frames():
    import numpy as np

    seg = Segmenter()
    quiet = [np.full(480, 0.0005, dtype=np.float32)] * 30
    loud = [np.full(480, 0.2, dtype=np.float32)] * 40
    results = [seg.feed(f) for f in quiet + loud + quiet]
    assert sum(r is not None for r in results) == 1


class FakeGrokApp:
    def __init__(self, running=False, fail=False):
        self.calls, self.running, self.fail = [], running, fail

    def start_voice_chat(self, bot=""):
        self.calls.append(bot)
        if self.fail:
            raise RuntimeError("no button")
        return not self.running

    def in_voice_chat(self):
        return self.running


def _voice_chat_listener(monkeypatch, app, paused=False):
    sounds, said, logs = [], [], []
    monkeypatch.setattr(voice, "chime", sounds.append)
    monkeypatch.setattr(voice, "speak", said.append)
    cfg = {"wake_phrases": [], "wake_action": "voice_chat", "grok_bot_name": "Elon"}
    return Listener(cfg, log=logs.append, is_paused=lambda: paused, grok_app=app), sounds, said, logs


def test_wake_phrase_opens_grok_voice_chat(monkeypatch):
    app = FakeGrokApp()
    listener, sounds, said, _ = _voice_chat_listener(monkeypatch, app)
    assert listener.handle("Hey Elon.", awaiting_command=False) is False
    assert listener.handle("hey elon open safari", awaiting_command=False) is False
    assert listener.handle("random chatter", awaiting_command=True) is False
    assert app.calls == ["Elon", "Elon"]
    assert sounds == ["Pop", "Pop"] and said == []


def test_voice_chat_already_open_is_quiet(monkeypatch):
    app = FakeGrokApp(running=True)
    listener, sounds, said, _ = _voice_chat_listener(monkeypatch, app)
    listener.handle("hey elon", awaiting_command=False)
    assert sounds == [] and said == []


def test_voice_chat_failure_is_spoken_and_logged(monkeypatch):
    listener, _, said, logs = _voice_chat_listener(monkeypatch, FakeGrokApp(fail=True))
    listener.handle("hey elon", awaiting_command=False)
    assert said == ["Sorry, I couldn't open Grok Bot."]
    assert "no button" in logs[0]


def test_voice_chat_not_opened_while_paused(monkeypatch):
    app = FakeGrokApp()
    listener, _, said, _ = _voice_chat_listener(monkeypatch, app, paused=True)
    listener.handle("hey elon", awaiting_command=False)
    assert app.calls == [] and said == [voice.PAUSED_MESSAGE]


class Node:
    def __init__(self, label="", *children):
        self.label, self.children = label, list(children)


def test_find_button_matches_bot_names():
    from jarvis.grokbot import find_button

    elon = Node("Elon, Main Bot")
    voice_btn = Node("Start voice chat")
    tree = Node("", Node("", Node("Mr Busy, Unread activity"), elon), Node("", voice_btn))
    find = lambda wanted: find_button(tree, wanted, lambda n: n.children, lambda n: [n.label] if n.label else [])
    assert find("elon") is elon
    assert find("Start voice chat") is voice_btn
    assert find("Ben") is None


@pytest.mark.parametrize("cfg,expected", [
    ({"wake_phrases": [], "grok_bot_name": "Elon"}, ["hey elon"]),
    ({"wake_phrases": ["hey jarvis"], "grok_bot_name": "Elon"}, ["hey elon"]),   # old default
    ({"wake_phrases": ["yo friday"], "grok_bot_name": "Elon"}, ["yo friday"]),  # user's own choice
    ({"wake_phrases": [], "grok_bot_name": ""}, ["hey grok"]),
])
def test_wake_phrase_follows_bot_name(cfg, expected):
    from jarvis.config import wake_phrases

    assert wake_phrases(cfg) == expected


def test_hey_bot_name_opens_that_bots_voice_chat(monkeypatch):
    app = FakeGrokApp()
    monkeypatch.setattr(voice, "chime", lambda *a: None)
    cfg = {"wake_phrases": [], "wake_action": "voice_chat", "grok_bot_name": "Elon"}
    listener = Listener(cfg, log=lambda m: None, is_paused=lambda: False, grok_app=app)
    listener.handle("Hey Jarvis.", awaiting_command=False)
    assert app.calls == []
    listener.handle("Hey, Elon.", awaiting_command=False)
    assert app.calls == ["Elon"]


def test_setup_and_wake_cli(tmp_path, monkeypatch, capsys):
    from jarvis import __main__ as cli
    from jarvis import config

    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "activity.log")
    monkeypatch.setattr("builtins.input", lambda prompt: "Elon")
    assert cli.main(["voice", "setup"]) == 0
    assert 'Say "hey elon"' in capsys.readouterr().out
    assert cli.main(["wake", "yo friday"]) == 0
    assert config.wake_phrases(config.load()) == ["yo friday"]
    assert cli.main(["wake", "auto"]) == 0
    assert config.wake_phrases(config.load()) == ["hey elon"]
