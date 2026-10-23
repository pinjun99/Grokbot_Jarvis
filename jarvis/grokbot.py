"""Opens a live voice chat in the Grok Bot app, so Grok answers in its own voice.

Grok Bot is an Electron app. Its buttons ("Start voice chat", one per bot such as
"Elon, Main Bot") only show up to macOS accessibility after we set
AXManualAccessibility on it, which is what screen readers do too.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterable

APP_NAME = "Grok Bot"
VOICE_CHAT_BUTTON = "Start voice chat"
HANG_UP_BUTTON = "Hang up"  # only there while a voice chat is running


def label_matches(label: str, wanted: str) -> bool:
    """Bot buttons are labelled "Elon, Main Bot" or "Mr Busy, Unread activity"."""
    label, wanted = label.strip().lower(), wanted.strip().lower()
    return label == wanted or label.split(",")[0].strip() == wanted


def find_button(root, wanted: str, children: Callable[[object], Iterable], labels: Callable[[object], list[str]],
                max_depth: int = 60):
    """Breadth-first search for a button whose title or description matches `wanted`."""
    level = [root]
    for _ in range(max_depth):
        next_level = []
        for el in level:
            if any(label_matches(lab, wanted) for lab in labels(el)):
                return el
            next_level.extend(children(el))
        if not next_level:
            return None
        level = next_level
    return None


class GrokBotApp:
    """The real Grok Bot app, driven through the macOS accessibility API."""

    def _app(self, launch: bool):
        from AppKit import NSWorkspace

        for _ in range(40 if launch else 1):
            for app in NSWorkspace.sharedWorkspace().runningApplications():
                if app.localizedName() == APP_NAME:
                    return app
            if launch:
                subprocess.run(["open", "-a", APP_NAME], capture_output=True)
                time.sleep(0.5)
        raise RuntimeError(f"Couldn't open the {APP_NAME} app. Is it installed?")

    @staticmethod
    def _get(el, attr: str):
        from ApplicationServices import AXUIElementCopyAttributeValue

        err, value = AXUIElementCopyAttributeValue(el, attr, None)
        return value if err == 0 else None

    def _labels(self, el) -> list[str]:
        if self._get(el, "AXRole") != "AXButton":
            return []
        return [str(v) for v in (self._get(el, "AXTitle"), self._get(el, "AXDescription")) if v]

    def _children(self, el) -> list:
        if self._get(el, "AXRole") == "AXMenuBar":
            return []
        return list(self._get(el, "AXChildren") or [])

    def _press(self, root, wanted: str, wait: float) -> bool:
        from ApplicationServices import AXUIElementPerformAction

        deadline = time.monotonic() + wait
        while True:
            button = find_button(root, wanted, self._children, self._labels)
            if button is not None:
                AXUIElementPerformAction(button, "AXPress")
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.3)

    def _root(self, app):
        from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetAttributeValue

        root = AXUIElementCreateApplication(app.processIdentifier())
        AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)
        return root

    def in_voice_chat(self) -> bool:
        try:
            app = self._app(launch=False)
        except RuntimeError:
            return False
        return find_button(self._root(app), HANG_UP_BUTTON, self._children, self._labels) is not None

    def start_voice_chat(self, bot: str = "") -> bool:
        """Returns False if a voice chat was already running."""
        from AppKit import NSApplicationActivateIgnoringOtherApps

        app = self._app(launch=True)
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        root = self._root(app)
        if find_button(root, HANG_UP_BUTTON, self._children, self._labels) is not None:
            return False
        if bot:
            if not self._press(root, bot, wait=8):
                raise RuntimeError(f"Couldn't find a bot called {bot!r} in {APP_NAME}.")
            time.sleep(1.0)  # let the chat open
        if not self._press(root, VOICE_CHAT_BUTTON, wait=8):
            raise RuntimeError(f"Couldn't find {APP_NAME}'s voice chat button.")
        return True
