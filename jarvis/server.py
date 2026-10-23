"""The connector Grok talks to. Each tool here is something Grok can do on your Mac.

Grok stays the brain: it decides what to do. Jarvis only carries the action
out, after checking it against your permission mode.
"""

from __future__ import annotations

import functools
import hmac
import json
import secrets
import threading
import time

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from .mac import parse_keys
from .permissions import MODES, Gatekeeper, Risk, applescript_risk, keys_risk, shell_risk, url_risk

INSTRUCTIONS = """\
You are connected to Jarvis, a bridge to the user's own Mac (Apple Silicon, macOS 26). \
Through these tools you can see the screen and use the mouse, keyboard and apps, like Jarvis \
from Iron Man. You do the thinking; Jarvis carries out your actions.

How to work well:
1. Prefer direct tools over clicking: open_app, open_url and run_applescript are fast and \
reliable. Use the mouse and keyboard only when there is no direct way.
2. Before clicking, look first: call screenshot (or read_screen_text to get exact click \
positions for visible text). Coordinates are pixels in the latest screenshot, origin top-left.
3. After each action, take another screenshot to check it worked before moving on.
4. The user chooses a permission mode. Some actions pop up an approval dialog on the Mac; \
if the user says no, stop and tell them. Never try to get around a refusal another way.
5. Before anything with real-world consequences - sending a message or email, posting, \
buying or paying, deleting files, changing account or security settings - call \
ask_user_permission with a clear one-line summary first, even if the mode would allow it.
6. Keep replies short. Use speak for brief spoken updates when the user is talking to you \
by voice.
"""

_OBSERVE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
_ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_DANGER = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


class ShotStore:
    """Keeps recent screenshots briefly so Grok can also open them as a link.

    Each link uses its own random id (not the bridge key) and expires after a few minutes.
    """

    def __init__(self, ttl: float = 600, keep: int = 20, clock=time.monotonic):
        self.ttl = ttl
        self.keep = keep
        self.clock = clock
        self.base_url = ""  # public address, set once the tunnel is up
        self._shots: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def add(self, data: bytes) -> str:
        shot_id = secrets.token_urlsafe(16)
        with self._lock:
            self._shots[shot_id] = (self.clock(), data)
            while len(self._shots) > self.keep:
                self._shots.pop(next(iter(self._shots)))
        return f"{self.base_url.rstrip('/')}/shots/{shot_id}.jpg"

    def get(self, shot_id: str) -> bytes | None:
        with self._lock:
            entry = self._shots.get(shot_id)
            if entry and self.clock() - entry[0] <= self.ttl:
                return entry[1]
            self._shots.pop(shot_id, None)
            return None


def build_server(mac, gate: Gatekeeper, shots: ShotStore | None = None) -> MCPServer:
    """`mac` is a MacController (or a fake one in tests)."""
    server = MCPServer(name="jarvis", title="Jarvis - Mac control", instructions=INSTRUCTIONS, version="0.2.0",
                       log_level="WARNING")

    def tool(annotations: ToolAnnotations):
        register = server.tool(annotations=annotations, structured_output=False)

        def decorator(fn):
            # Pass every failure's message on to Grok (the SDK hides it otherwise),
            # so it knows *why*, e.g. "the user said no" or "permission missing".
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except Exception as e:
                    raise ToolError(str(e) or type(e).__name__) from e

            return register(wrapper)

        return decorator

    # ---------- looking ----------

    @tool(_OBSERVE)
    def get_status() -> str:
        """What Jarvis can currently do: permission mode, screen size and the app in front."""
        w, h = mac.screenshot_size()
        apps = mac.running_apps()
        return json.dumps({
            "mode": gate.mode(),
            "mode_meaning": MODES[gate.mode()],
            "paused": gate.is_paused(),
            "screenshot_size": [w, h],
            "frontmost_app": apps["frontmost"],
        })

    @tool(_OBSERVE)
    def screenshot() -> list:
        """See the screen. Coordinates in this image are what click, drag and scroll expect."""
        gate.check(Risk.OBSERVE, "Take a screenshot")
        shot = mac.screenshot()
        front = mac.running_apps()["frontmost"]
        note = f"Screenshot is {shot.width}x{shot.height} pixels. Frontmost app: {front}."
        if shots is not None:
            note += f" If you can't see the image above, open it here (valid 10 minutes): {shots.add(shot.data)}"
        return [Image(data=shot.data, format="jpeg"), note]

    @tool(_OBSERVE)
    def read_screen_text() -> str:
        """Read all visible text on screen (on-device OCR), each with the x,y to click it."""
        gate.check(Risk.OBSERVE, "Read the text on screen")
        return json.dumps(mac.read_screen_text(), ensure_ascii=False)

    @tool(_OBSERVE)
    def list_apps() -> str:
        """Which apps are open, and which one is in front."""
        gate.check(Risk.OBSERVE, "List open apps")
        return json.dumps(mac.running_apps())

    # ---------- doing ----------

    @tool(_ACT)
    def open_app(name: str) -> str:
        """Open (or switch to) an app by name, e.g. "Safari", "Claude", "Notes", "Spotify"."""
        gate.check(Risk.CONTROL, f"Open the app: {name}")
        mac.open_app(name)
        return f"Opened {name}."

    @tool(_ACT)
    def open_url(url: str) -> str:
        """Open a website or link in the default browser, e.g. "https://youtube.com"."""
        gate.check(url_risk(url), f"Open: {url}")
        mac.open_url(url)
        return f"Opened {url}."

    @tool(_ACT)
    def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        """Click at x,y (screenshot pixels). button: left/right/middle. clicks: 2 = double-click."""
        if clicks not in (1, 2, 3):
            raise ValueError("clicks must be 1, 2 or 3")
        gate.check(Risk.CONTROL, f"{'Double-' if clicks == 2 else ''}{button}-click at ({x}, {y})")
        mac.click(x, y, button, clicks)
        return "Clicked."

    @tool(_ACT)
    def move_mouse(x: int, y: int) -> str:
        """Move the pointer to x,y without clicking (e.g. to show a hover menu)."""
        gate.check(Risk.CONTROL, f"Move the mouse to ({x}, {y})")
        mac.move_mouse(x, y)
        return "Moved."

    @tool(_ACT)
    def drag(from_x: int, from_y: int, to_x: int, to_y: int) -> str:
        """Press, drag and release, e.g. to move a file or select text."""
        gate.check(Risk.CONTROL, f"Drag from ({from_x}, {from_y}) to ({to_x}, {to_y})")
        mac.drag(from_x, from_y, to_x, to_y)
        return "Dragged."

    @tool(_ACT)
    def scroll(direction: str, amount: int = 5, x: int | None = None, y: int | None = None) -> str:
        """Scroll up/down/left/right. amount is in lines. Optionally scroll over a point x,y."""
        gate.check(Risk.CONTROL, f"Scroll {direction}")
        mac.scroll(direction, max(1, min(amount, 50)), x, y)
        return "Scrolled."

    @tool(_ACT)
    def type_text(text: str) -> str:
        """Type text into whatever is focused. Newlines press Return. Click the field first."""
        preview = text if len(text) <= 200 else text[:200] + "..."
        gate.check(Risk.CONTROL, f"Type: {preview}")
        mac.type_text(text)
        return f"Typed {len(text)} characters."

    @tool(_ACT)
    def press_keys(keys: str) -> str:
        """Press a key or shortcut, e.g. "return", "escape", "cmd+t", "cmd+shift+4", "down"."""
        mods, key = parse_keys(keys)
        gate.check(keys_risk(mods, key), f"Press keys: {keys}")
        mac.press_keys(keys)
        return f"Pressed {keys}."

    @tool(_ACT)
    def wait(seconds: float) -> str:
        """Wait for something to load (max 30 seconds)."""
        time.sleep(max(0.0, min(seconds, 30.0)))
        return "Done waiting."

    @tool(_ACT)
    def speak(text: str) -> str:
        """Say something out loud on the Mac in Jarvis's voice. Keep it short."""
        gate.check(Risk.CONTROL, f"Say out loud: {text[:200]}")
        mac.speak(text)
        return "Speaking."

    @tool(_ACT)
    def notify(title: str, message: str) -> str:
        """Show a macOS notification banner."""
        gate.check(Risk.CONTROL, f"Show notification: {title}")
        mac.notify(title, message)
        return "Notification shown."

    @tool(_ACT)
    def set_clipboard(text: str) -> str:
        """Put text on the clipboard (then press cmd+v to paste it)."""
        gate.check(Risk.CONTROL, "Copy text to the clipboard")
        mac.set_clipboard(text)
        return "Copied."

    # ---------- sensitive ----------

    @tool(_OBSERVE)
    def get_clipboard() -> str:
        """Read what the user last copied. May contain private data, so it may need approval."""
        gate.check(Risk.SENSITIVE, "Read your clipboard")
        return mac.get_clipboard()

    @tool(_DANGER)
    def quit_app(name: str) -> str:
        """Quit an app. Unsaved work may be lost."""
        gate.check(Risk.SENSITIVE, f"Quit the app: {name}")
        mac.quit_app(name)
        return f"Asked {name} to quit."

    @tool(_DANGER)
    def run_applescript(script: str) -> str:
        """Run AppleScript to control apps directly (Safari tabs, Music, Finder, Mail drafts...)."""
        gate.check(applescript_risk(script), f"Run AppleScript:\n{script[:600]}")
        return mac.run_applescript(script) or "Done (no output)."

    @tool(_DANGER)
    def run_shell(command: str) -> str:
        """Run a Terminal (zsh) command in the home folder. 60 second limit."""
        gate.check(shell_risk(command), f"Run Terminal command:\n{command[:600]}")
        return json.dumps(mac.run_shell(command))

    @tool(_OBSERVE)
    def ask_user_permission(action_summary: str) -> str:
        """Ask the user to approve an important action via a pop-up on their Mac. Call this
        BEFORE sending, posting, buying, paying, deleting, or changing settings."""
        gate.ask(action_summary)
        return "The user approved. Go ahead."

    return server


class KeyGuard:
    """Lets requests through only with the secret key, either as an
    `Authorization: Bearer <key>` header on /mcp or in the URL path (/<key>/mcp).
    Also serves screenshot links from the ShotStore."""

    def __init__(self, app: ASGIApp, key: str, shots: ShotStore | None = None):
        self.app = app
        self.key = key
        self.shots = shots
        self.secret_path = f"/{key}/mcp"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/") or "/"
        if path == "/":
            return await self._plain(send, 200, b"Jarvis is online.")
        if self.shots and path.startswith("/shots/") and path.endswith(".jpg"):
            data = self.shots.get(path[len("/shots/"):-len(".jpg")])
            if data is not None:
                return await self._plain(send, 200, data, b"image/jpeg")
        headers = dict(scope.get("headers") or [])
        bearer = headers.get(b"authorization", b"").decode()
        if hmac.compare_digest(path, self.secret_path) or (
            path == "/mcp" and hmac.compare_digest(bearer, f"Bearer {self.key}")
        ):
            scope = {**scope, "path": self.secret_path, "raw_path": self.secret_path.encode()}
            return await self.app(scope, receive, send)
        return await self._plain(send, 404, b"Not found.")

    @staticmethod
    async def _plain(send: Send, status: int, body: bytes, content_type: bytes = b"text/plain") -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def build_app(server: MCPServer, key: str, shots: ShotStore | None = None) -> ASGIApp:
    app = server.streamable_http_app(
        streamable_http_path=f"/{key}/mcp",
        stateless_http=True,
        json_response=True,
        # Requests arrive through the tunnel with its public hostname; the secret key
        # (checked by KeyGuard) is what protects the server, not the Host header.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return KeyGuard(app, key, shots)
