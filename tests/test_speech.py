import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jarvis import __main__ as cli
from jarvis import config, speech, voice
from jarvis.speech import Speaker, provider


class FakeMacAudio:
    """Stands in for `afplay` and `say`: records what would have played."""

    def __init__(self, afplay_exit=0):
        self.played = []
        self.afplay_exit = afplay_exit

    def __call__(self, args):
        if args[0] == "afplay":
            self.played.append(("afplay", Path(args[1]).read_bytes()))
            return type("Done", (), {"returncode": self.afplay_exit})()
        self.played.append(tuple(args))
        return type("Done", (), {"returncode": 0})()


def _speaker(cfg, fetch=None, audio=None):
    logs = []
    audio = audio or FakeMacAudio()
    fetch = fetch or (lambda text, key, voice_id, language: b"MP3:" + text.encode())
    return Speaker(get_cfg=lambda: cfg, log=logs.append, fetch=fetch, run=audio), audio, logs


GROK = {"speech_voice_provider": "auto", "xai_api_key": "xai-test", "grok_voice": "rex", "voice": "Daniel"}


def test_provider_choice():
    assert provider({"speech_voice_provider": "auto", "xai_api_key": ""}) == "mac"
    assert provider({"speech_voice_provider": "auto", "xai_api_key": "xai-1"}) == "grok"
    assert provider({"speech_voice_provider": "mac", "xai_api_key": "xai-1"}) == "mac"
    assert provider({"speech_voice_provider": "grok", "xai_api_key": ""}) == "grok"


def test_grok_voice_plays_with_afplay():
    calls = []

    def fetch(text, key, voice_id, language):
        calls.append((text, key, voice_id, language))
        return b"MP3DATA"

    s, audio, logs = _speaker(GROK, fetch)
    assert s.say_now("Hello sir") is None
    assert calls == [("Hello sir", "xai-test", "rex", "auto")]
    assert audio.played == [("afplay", b"MP3DATA")]
    assert logs == []


def test_no_key_falls_back_to_say():
    s, audio, logs = _speaker({**GROK, "speech_voice_provider": "grok", "xai_api_key": ""})
    assert "no xAI API key" in s.say_now("Hi")
    assert audio.played == [("say", "-v", "Daniel", "Hi")]
    assert "no xAI API key" in logs[0]


def test_mac_provider_never_calls_xai():
    def fetch(*a):
        raise AssertionError("should not call xAI")

    s, audio, logs = _speaker({**GROK, "speech_voice_provider": "mac"}, fetch)
    s.say_now("Hi")
    assert audio.played == [("say", "-v", "Daniel", "Hi")]
    assert logs == []


def test_network_error_falls_back_to_say():
    def fetch(*a):
        raise OSError("no internet")

    s, audio, logs = _speaker(GROK, fetch)
    assert s.say_now("Hi") == "no internet"
    assert audio.played == [("say", "-v", "Daniel", "Hi")]
    assert "no internet" in logs[0]


def test_afplay_failure_falls_back_to_say():
    s, audio, _ = _speaker(GROK, audio=FakeMacAudio(afplay_exit=1))
    assert "afplay" in s.say_now("Hi")
    assert audio.played[-1] == ("say", "-v", "Daniel", "Hi")


def test_say_is_non_blocking_and_one_at_a_time():
    active, overlaps, order = [0], [], []
    lock = threading.Lock()

    def slow_fetch(text, *a):
        with lock:
            active[0] += 1
            overlaps.append(active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        order.append(text)
        return b"x"

    s, audio, _ = _speaker(GROK, slow_fetch)
    start = time.monotonic()
    for word in ["one", "two", "three"]:
        s.say(word)
    assert time.monotonic() - start < 0.05      # returned straight away
    s.wait()
    assert order == ["one", "two", "three"]
    assert max(overlaps) == 1                    # never two at once
    assert len(audio.played) == 3


@pytest.fixture
def xai():
    """A pretend api.x.ai on localhost."""
    seen = []
    state = {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, body, ctype):
            self.send_response(state["status"])
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers["Authorization"], body))
            if state["status"] != 200:
                return self._reply(b'{"error":"bad key"}', "application/json")
            self._reply(b"ID3fake-mp3", "audio/mpeg")

        def do_GET(self):
            seen.append((self.path, self.headers["Authorization"], None))
            self._reply(json.dumps({"voices": [{"voice_id": "eve", "name": "Eve", "language": "en"},
                                               {"voice_id": "rex", "name": "Rex", "language": "en"}]}).encode(),
                        "application/json")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    old = speech.TTS_URL, speech.VOICES_URL
    speech.TTS_URL, speech.VOICES_URL = f"{base}/v1/tts", f"{base}/v1/tts/voices"
    yield seen, state
    speech.TTS_URL, speech.VOICES_URL = old
    server.shutdown()


def test_fetch_tts_request_shape(xai):
    seen, _ = xai
    assert speech.fetch_tts("Good evening", "xai-k", "ara", "en") == b"ID3fake-mp3"
    assert seen == [("/v1/tts", "Bearer xai-k", {"text": "Good evening", "voice_id": "ara", "language": "en"})]


def test_fetch_tts_http_error_is_readable(xai):
    _, state = xai
    state["status"] = 401
    with pytest.raises(RuntimeError, match="didn't accept the API key.*401"):
        speech.fetch_tts("Hi", "xai-wrong")


def test_list_voices(xai):
    assert [v["voice_id"] for v in speech.list_voices("xai-k")] == ["eve", "rex"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "activity.log")
    return tmp_path


def test_defaults_use_mac_voice_until_key_set(home):
    cfg = config.load()
    assert cfg["grok_voice"] == "eve" and cfg["xai_api_key"] == ""
    assert provider(cfg) == "mac"


def test_cli_grok_key(home, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt: " xai-abc123 ")
    assert cli.main(["voice", "grok-key"]) == 0
    cfg = config.load()
    assert cfg["xai_api_key"] == "xai-abc123"
    assert cfg["speech_voice_provider"] == "grok"
    assert (home / "config.json").stat().st_mode & 0o777 == 0o600
    assert "xai-abc123" not in capsys.readouterr().out


def test_cli_speaker_lists_and_sets(home, xai, capsys):
    config.update(xai_api_key="xai-k")
    assert cli.main(["voice", "speaker"]) == 0
    out = capsys.readouterr().out
    assert "► eve" in out and "rex" in out
    assert cli.main(["voice", "speaker", "Rex"]) == 0
    assert config.load()["grok_voice"] == "rex"
    assert cli.main(["voice", "speaker", "mac"]) == 0
    assert provider(config.load()) == "mac"


def test_cli_say_reports_fallback(home, monkeypatch, capsys):
    audio = FakeMacAudio()
    monkeypatch.setattr(speech.subprocess, "run", audio)
    config.update(speech_voice_provider="grok")
    assert cli.main(["voice", "say", "hello"]) == 1
    assert "Mac voice" in capsys.readouterr().out
    assert audio.played == [("say", "-v", "Daniel", "hello")]


def test_listener_speaks_through_speech(monkeypatch):
    said = []
    monkeypatch.setattr(voice, "speak", said.append)
    monkeypatch.setattr(voice, "chime", lambda *a: None)
    cfg = {"wake_phrases": [], "grok_bot_name": "Elon", "wake_action": "webhook", "voice_webhook_url": "http://127.0.0.1:1/nope",
           "voice_webhook_headers": {}}
    paused = [True]
    listener = voice.Listener(cfg, log=lambda m: None, is_paused=lambda: paused[0])
    listener.handle("hey elon open safari", awaiting_command=False)
    paused[0] = False
    listener.handle("hey elon open safari", awaiting_command=False)
    assert said == [voice.PAUSED_MESSAGE, "Sorry, I couldn't reach Grok."]
