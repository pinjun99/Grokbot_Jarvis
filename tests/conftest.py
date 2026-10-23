import socket
import threading
import time

import pytest
import uvicorn

from jarvis.mac import Screenshot
from jarvis.permissions import Gatekeeper
from jarvis.server import ShotStore, build_app, build_server

KEY = "test-secret-key"


class FakeMac:
    """Records what Grok asked for instead of touching a real Mac."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
        return record

    def screenshot_size(self):
        return 1280, 800

    def screenshot(self):
        return Screenshot(b"\xff\xd8fakejpeg", "image/jpeg", 1280, 800)

    def running_apps(self):
        return {"frontmost": "Safari", "running": ["Finder", "Safari"]}

    def read_screen_text(self):
        return [{"text": "Sign in", "x": 100, "y": 200}]

    def get_clipboard(self):
        return "copied text"

    def run_shell(self, command):
        self.calls.append(("run_shell", command))
        return {"exit_code": 0, "stdout": "hi\n", "stderr": ""}

    def run_applescript(self, script):
        self.calls.append(("run_applescript", script))
        return "ok"


class Harness:
    def __init__(self):
        self.mac = FakeMac()
        self.mode = "assist"
        self.paused = False
        self.answers = []      # what the "user" clicks in pop-ups, in order
        self.prompts = []      # what the pop-ups said
        self.gate = Gatekeeper(
            get_mode=lambda: self.mode,
            is_paused=lambda: self.paused,
            confirm=self._confirm,
        )

    def _confirm(self, message, allow_session):
        self.prompts.append((message, allow_session))
        return self.answers.pop(0) if self.answers else "deny"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def harness():
    h = Harness()
    port = _free_port()
    h.shots = ShotStore()
    app = build_app(build_server(h.mac, h.gate, h.shots), KEY, h.shots)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    h.base = f"http://127.0.0.1:{port}"
    h.shots.base_url = h.base
    h.url = f"{h.base}/{KEY}/mcp"
    yield h
    server.should_exit = True
    thread.join(timeout=5)
