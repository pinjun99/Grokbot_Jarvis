"""The hands and eyes: everything that actually touches the Mac.

All macOS-specific imports happen inside the functions so the rest of
Jarvis (permissions, server, tests) can be imported on any machine.

Coordinate system: Grok only ever sees screenshots, so every x/y it sends
is in *screenshot pixels*. MacController converts those to macOS "points"
(the units Quartz uses) with a fixed scale factor, so the numbers Grok reads
off a screenshot are exactly the numbers it should click.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

# macOS virtual key codes (Carbon kVK_* constants).
KEY_CODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "return": 36,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
    "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "backspace": 51, "escape": 53,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90, "f5": 96, "f6": 97, "f7": 98, "f3": 99,
    "f8": 100, "f9": 101, "f11": 103, "f13": 105, "f16": 106, "f14": 107, "f10": 109,
    "f12": 111, "f15": 113, "home": 115, "pageup": 116, "forwarddelete": 117, "f4": 118,
    "end": 119, "f2": 120, "pagedown": 121, "f1": 122, "left": 123, "right": 124,
    "down": 125, "up": 126,
}

KEY_ALIASES: dict[str, str] = {
    "enter": "return", "esc": "escape", "delete": "backspace", "del": "forwarddelete",
    "spacebar": "space", "arrowleft": "left", "arrowright": "right", "arrowup": "up",
    "arrowdown": "down", "pgup": "pageup", "pgdn": "pagedown",
}

# modifier name -> (key code, Quartz flag attribute name)
MODIFIERS: dict[str, tuple[int, str]] = {
    "cmd": (55, "kCGEventFlagMaskCommand"),
    "shift": (56, "kCGEventFlagMaskShift"),
    "option": (58, "kCGEventFlagMaskAlternate"),
    "ctrl": (59, "kCGEventFlagMaskControl"),
    "fn": (63, "kCGEventFlagMaskSecondaryFn"),
}

MODIFIER_ALIASES: dict[str, str] = {
    "command": "cmd", "meta": "cmd", "super": "cmd", "win": "cmd", "⌘": "cmd",
    "alt": "option", "opt": "option", "⌥": "option",
    "control": "ctrl", "⌃": "ctrl", "⇧": "shift",
}


def parse_keys(combo: str) -> tuple[list[str], str]:
    """Split "cmd+shift+t" into (["cmd", "shift"], "t"). Raises ValueError if unknown."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("No keys given")
    *mods, key = parts
    mods = [MODIFIER_ALIASES.get(m, m) for m in mods]
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"Unknown modifier key: {m!r}. Use cmd, shift, option, ctrl or fn.")
    key = KEY_ALIASES.get(key, key)
    if key in MODIFIER_ALIASES or key in MODIFIERS:
        raise ValueError("The last key in a combo must be a normal key, not a modifier")
    if key not in KEY_CODES:
        raise ValueError(f"Unknown key: {key!r}")
    return mods, key


@dataclass
class Screenshot:
    data: bytes
    mime: str
    width: int
    height: int


def screenshot_scale(points_width: float, max_width: int) -> float:
    """Screenshot pixels per screen point (<= 1, so screenshots never exceed the logical size)."""
    return min(1.0, max_width / points_width)


def _run(args: list[str], timeout: float = 30, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input)


def run_osascript(script: str, *argv: str, timeout: float = 30) -> str:
    """Run AppleScript. Extra argv are passed safely (no string escaping needed)."""
    proc = _run(["osascript", "-e", script, *argv], timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "AppleScript failed")
    return proc.stdout.strip()


class MacController:
    """Real macOS implementation. Every method is synchronous and blocking."""

    def __init__(self, max_width: int = 1280, voice: str = "Daniel"):
        self.max_width = max_width
        self.voice = voice

    # ---------- screen geometry ----------

    def screen_size_points(self) -> tuple[float, float]:
        import Quartz

        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return bounds.size.width, bounds.size.height

    @property
    def scale(self) -> float:
        return screenshot_scale(self.screen_size_points()[0], self.max_width)

    def screenshot_size(self) -> tuple[int, int]:
        w, h = self.screen_size_points()
        s = self.scale
        return round(w * s), round(h * s)

    def _to_points(self, x: float, y: float) -> tuple[float, float]:
        sw, sh = self.screenshot_size()
        if not (0 <= x <= sw and 0 <= y <= sh):
            raise ValueError(f"({x}, {y}) is outside the screen. The screenshot is {sw}x{sh}.")
        s = self.scale
        return x / s, y / s

    # ---------- eyes ----------

    def _capture_png(self) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="jarvis-")
        os.close(fd)
        try:
            # -x: no shutter sound, -C: include cursor, -m: main display only
            proc = _run(["screencapture", "-x", "-C", "-m", "-t", "png", path])
            if proc.returncode != 0 or os.path.getsize(path) == 0:
                raise RuntimeError(
                    "Could not take a screenshot. Give your Terminal app 'Screen Recording' permission "
                    "in System Settings > Privacy & Security, then restart Jarvis."
                )
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def screenshot(self, quality: int = 70) -> Screenshot:
        from PIL import Image

        img = Image.open(io.BytesIO(self._capture_png())).convert("RGB")
        w, h = self.screenshot_size()
        img = img.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return Screenshot(buf.getvalue(), "image/jpeg", w, h)

    def read_screen_text(self) -> list[dict]:
        """On-device OCR (Apple Vision). Returns text with click points in screenshot pixels."""
        import Vision
        from Foundation import NSData

        png = self._capture_png()
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(png, len(png)), None
        )
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Text recognition failed: {err}")
        w, h = self.screenshot_size()
        items = []
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            box = obs.boundingBox()  # normalised 0-1, origin bottom-left
            cx = (box.origin.x + box.size.width / 2) * w
            cy = (1 - (box.origin.y + box.size.height / 2)) * h
            items.append({"text": str(candidates[0].string()), "x": round(cx), "y": round(cy)})
        items.sort(key=lambda i: (i["y"] // 10, i["x"]))
        return items

    # ---------- mouse ----------

    def _post_mouse(self, kind: str, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        import Quartz

        buttons = {
            "left": (Quartz.kCGMouseButtonLeft, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp,
                     Quartz.kCGEventLeftMouseDragged),
            "right": (Quartz.kCGMouseButtonRight, Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp,
                      Quartz.kCGEventRightMouseDragged),
            "middle": (Quartz.kCGMouseButtonCenter, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp,
                       Quartz.kCGEventOtherMouseDragged),
        }
        if button not in buttons:
            raise ValueError("button must be left, right or middle")
        btn, down, up, dragged = buttons[button]
        event_type = {"move": Quartz.kCGEventMouseMoved, "down": down, "up": up, "drag": dragged}[kind]
        ev = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), btn)
        if kind in ("down", "up"):
            Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, clicks)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def move_mouse(self, x: float, y: float) -> None:
        px, py = self._to_points(x, y)
        self._post_mouse("move", px, py)

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        px, py = self._to_points(x, y)
        self._post_mouse("move", px, py)
        time.sleep(0.05)
        for n in range(1, clicks + 1):
            self._post_mouse("down", px, py, button, n)
            self._post_mouse("up", px, py, button, n)
            time.sleep(0.03)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        sx, sy = self._to_points(x1, y1)
        ex, ey = self._to_points(x2, y2)
        self._post_mouse("move", sx, sy)
        self._post_mouse("down", sx, sy)
        steps = 20
        for i in range(1, steps + 1):
            self._post_mouse("drag", sx + (ex - sx) * i / steps, sy + (ey - sy) * i / steps)
            time.sleep(0.01)
        self._post_mouse("up", ex, ey)

    def scroll(self, direction: str, amount: int = 5, x: float | None = None, y: float | None = None) -> None:
        import Quartz

        if x is not None and y is not None:
            self.move_mouse(x, y)
        dy = {"up": amount, "down": -amount}.get(direction, 0)
        dx = {"left": amount, "right": -amount}.get(direction, 0)
        if not (dx or dy):
            raise ValueError("direction must be up, down, left or right")
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, dy, dx)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    # ---------- keyboard ----------

    def press_keys(self, combo: str) -> None:
        import Quartz

        mods, key = parse_keys(combo)
        flags = 0
        for m in mods:
            flags |= getattr(Quartz, MODIFIERS[m][1])

        def post(code: int, down: bool, fl: int) -> None:
            ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            Quartz.CGEventSetFlags(ev, fl)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

        held = 0
        for m in mods:
            held |= getattr(Quartz, MODIFIERS[m][1])
            post(MODIFIERS[m][0], True, held)
        post(KEY_CODES[key], True, flags)
        post(KEY_CODES[key], False, flags)
        for m in reversed(mods):
            held &= ~getattr(Quartz, MODIFIERS[m][1])
            post(MODIFIERS[m][0], False, held)

    def type_text(self, text: str) -> None:
        """Types any text, including emoji and non-English characters."""
        import Quartz

        for line_no, line in enumerate(text.split("\n")):
            if line_no:
                self.press_keys("return")
            # Quartz accepts at most ~20 UTF-16 units per event
            for i in range(0, len(line), 16):
                chunk = line[i:i + 16]
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventSetFlags(ev, 0)
                    Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.01)

    # ---------- apps & system ----------

    def open_app(self, name: str) -> None:
        proc = _run(["open", "-a", name])
        if proc.returncode != 0:
            raise RuntimeError(f"Could not open {name!r}: {proc.stderr.strip()}")

    def quit_app(self, name: str) -> None:
        run_osascript("on run argv\ntell application (item 1 of argv) to quit\nend run", name)

    def open_url(self, url: str) -> None:
        proc = _run(["open", url])
        if proc.returncode != 0:
            raise RuntimeError(f"Could not open {url!r}: {proc.stderr.strip()}")

    def running_apps(self) -> dict:
        from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        apps = sorted(
            str(a.localizedName()) for a in ws.runningApplications()
            if a.activationPolicy() == NSApplicationActivationPolicyRegular
        )
        return {"frontmost": str(front.localizedName()) if front else None, "running": apps}

    def get_clipboard(self) -> str:
        return _run(["pbpaste"]).stdout

    def set_clipboard(self, text: str) -> None:
        _run(["pbcopy"], input=text)

    def run_applescript(self, script: str) -> str:
        return run_osascript(script, timeout=60)

    def run_shell(self, command: str, timeout: float = 60) -> dict:
        proc = subprocess.run(
            ["/bin/zsh", "-lc", command], capture_output=True, text=True, timeout=timeout,
            cwd=os.path.expanduser("~"),
        )
        return {"exit_code": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}

    def speak(self, text: str) -> None:
        # Non-blocking so Grok can carry on while Jarvis talks
        subprocess.Popen(["say", "-v", self.voice, text])

    def notify(self, title: str, message: str) -> None:
        run_osascript(
            "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run",
            title, message,
        )

    def confirm(self, message: str, allow_session: bool = True, timeout: int = 45) -> str:
        """Ask the human at the Mac. Returns 'deny', 'once' or 'session'."""
        buttons = '{"Deny", "Allow for 10 min", "Allow once"}' if allow_session else '{"Deny", "Allow once"}'
        script = (
            "on run argv\n"
            "activate\n"
            "set r to display dialog (item 1 of argv) with title \"Jarvis needs your OK\" "
            "buttons " + buttons + " default button \"Allow once\" "
            "cancel button \"Deny\" with icon caution giving up after " + str(int(timeout)) + "\n"
            "if gave up of r then return \"deny\"\n"
            "return button returned of r\n"
            "end run"
        )
        try:
            answer = run_osascript(script, message, timeout=timeout + 15)
        except Exception:
            return "deny"  # Deny button (cancel), closed dialog, or any failure
        return {"Allow once": "once", "Allow for 10 min": "session"}.get(answer, "deny")

    # ---------- health checks ----------

    def permissions(self) -> dict:
        import Quartz
        from ApplicationServices import AXIsProcessTrusted

        return {
            "accessibility": bool(AXIsProcessTrusted()),
            "screen_recording": bool(Quartz.CGPreflightScreenCaptureAccess()),
        }

    def request_permissions(self) -> None:
        """Pops the macOS permission prompts (only shown if not already granted)."""
        import Quartz
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        Quartz.CGRequestScreenCaptureAccess()
