"""Permission layers: decides whether Grok may do something on its own.

Every action Grok asks for has a risk level. The mode you choose decides,
per risk level, whether the action just happens, needs your OK in a pop-up
on the Mac, or is refused. Grok cannot change the mode; only you can, with
`jarvis mode <name>` in Terminal.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from enum import IntEnum


class Risk(IntEnum):
    OBSERVE = 0    # looking only: screenshots, reading screen text, listing apps
    CONTROL = 1    # normal use: clicking, typing, opening apps and websites
    SENSITIVE = 2  # could cause harm: shell commands, AppleScript, quitting apps, reading clipboard
    CRITICAL = 3   # serious: deleting, sending, buying, admin commands. Always asks.


MODES: dict[str, str] = {
    "watch": "Grok can only look at your screen. It cannot click, type or open anything.",
    "ask": "Grok can look freely, but asks you before every single action.",
    "assist": "Grok works normally on its own, and asks before anything sensitive or serious. (Recommended)",
    "autopilot": "Grok does almost everything on its own, and only asks before serious actions.",
}
DEFAULT_MODE = "assist"

# What each mode does with each risk level when no temporary approval is active.
_POLICY: dict[str, dict[Risk, str]] = {
    "watch": {Risk.OBSERVE: "allow", Risk.CONTROL: "deny", Risk.SENSITIVE: "deny", Risk.CRITICAL: "deny"},
    "ask": {Risk.OBSERVE: "allow", Risk.CONTROL: "confirm", Risk.SENSITIVE: "confirm", Risk.CRITICAL: "confirm"},
    "assist": {Risk.OBSERVE: "allow", Risk.CONTROL: "allow", Risk.SENSITIVE: "confirm", Risk.CRITICAL: "confirm"},
    "autopilot": {Risk.OBSERVE: "allow", Risk.CONTROL: "allow", Risk.SENSITIVE: "allow", Risk.CRITICAL: "confirm"},
}

SESSION_SECONDS = 10 * 60

# Shell commands that are serious no matter what.
_CRITICAL_SHELL = re.compile(
    r"(^|[;&|`(\s])("
    r"sudo|su|rm|rmdir|srm|shred|mkfs\w*|dd|diskutil|fdisk|shutdown|reboot|halt|launchctl|"
    r"chmod|chown|chflags|csrutil|spctl|security|defaults\s+delete|killall|pkill|kill|"
    r"crontab|passwd|dscl|networksetup|tmutil|osascript|mail|sendmail|curl|wget|scp|rsync|ssh|"
    r"git\s+push|npm\s+publish|pip\s+uninstall|brew\s+uninstall|trash"
    r")(?=$|[\s;&|)])"
    r"|>\s*/|\|\s*(sh|bash|zsh)\b|:\(\)\s*\{",
    re.IGNORECASE,
)

# AppleScript that sends, deletes, buys or escalates.
_CRITICAL_APPLESCRIPT = re.compile(
    r"\b(send|delete|empty\s+trash|do\s+shell\s+script|keystroke|key\s+code|password|"
    r"administrator|purchase|buy|pay|reply|forward|post|shut\s*down|restart|log\s*out|erase)\b",
    re.IGNORECASE,
)

# Key combos that close, delete or lock things.
_SENSITIVE_KEYS = {
    (frozenset({"cmd"}), "q"), (frozenset({"cmd"}), "w"), (frozenset({"cmd"}), "backspace"),
    (frozenset({"cmd", "option"}), "escape"), (frozenset({"cmd", "ctrl"}), "q"),
    (frozenset({"cmd", "shift"}), "q"), (frozenset({"cmd", "option"}), "w"),
}
_CRITICAL_KEYS = {
    (frozenset({"cmd", "shift"}), "backspace"), (frozenset({"cmd", "option", "shift"}), "backspace"),
    (frozenset({"cmd", "option"}), "backspace"),
}


def shell_risk(command: str) -> Risk:
    return Risk.CRITICAL if _CRITICAL_SHELL.search(command) else Risk.SENSITIVE


def applescript_risk(script: str) -> Risk:
    return Risk.CRITICAL if _CRITICAL_APPLESCRIPT.search(script) else Risk.SENSITIVE


def keys_risk(mods: list[str], key: str) -> Risk:
    combo = (frozenset(mods), key)
    if combo in _CRITICAL_KEYS:
        return Risk.CRITICAL
    if combo in _SENSITIVE_KEYS:
        return Risk.SENSITIVE
    return Risk.CONTROL


def url_risk(url: str) -> Risk:
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return Risk.CONTROL if scheme in ("http", "https", "mailto", "") else Risk.SENSITIVE


class PermissionDenied(Exception):
    pass


class Gatekeeper:
    """Checks every action against the current mode and asks the human when needed.

    `get_mode` and `is_paused` are read on every call, so changing the mode
    in Terminal takes effect immediately, even while Jarvis is running.
    """

    def __init__(
        self,
        get_mode: Callable[[], str],
        is_paused: Callable[[], bool],
        confirm: Callable[[str, bool], str],
        log: Callable[[str], None] = lambda _msg: None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.get_mode = get_mode
        self.is_paused = is_paused
        self.confirm = confirm
        self.log = log
        self.clock = clock
        self._session_until = 0.0

    def mode(self) -> str:
        mode = self.get_mode()
        return mode if mode in _POLICY else DEFAULT_MODE

    def session_active(self) -> bool:
        return self.clock() < self._session_until

    def decide(self, risk: Risk) -> str:
        """Returns 'allow', 'confirm' or 'deny' without asking anyone."""
        mode = self.mode()
        decision = _POLICY[mode][risk]
        # "Allow for 10 min" covers everything up to SENSITIVE, never CRITICAL.
        if decision == "confirm" and risk < Risk.CRITICAL and self.session_active():
            return "allow"
        return decision

    def check(self, risk: Risk, description: str) -> None:
        """Raises PermissionDenied unless the action may go ahead."""
        if self.is_paused():
            self.log(f"BLOCKED (paused): {description}")
            raise PermissionDenied("Jarvis is paused by the user. Ask them to run `jarvis resume`.")
        decision = self.decide(risk)
        if decision == "deny":
            self.log(f"DENIED ({self.mode()} mode): {description}")
            raise PermissionDenied(
                f"Not allowed in '{self.mode()}' mode. The user can change this with `jarvis mode`."
            )
        if decision == "confirm":
            self.ask(description, risk)
        else:
            self.log(f"ALLOWED: {description}")

    def ask(self, description: str, risk: Risk = Risk.CRITICAL) -> None:
        """Shows the pop-up on the Mac. Raises PermissionDenied if the user says no."""
        allow_session = risk < Risk.CRITICAL
        answer = self.confirm(f"Grok wants to:\n\n{description}", allow_session)
        if answer == "deny":
            self.log(f"USER SAID NO: {description}")
            raise PermissionDenied("The user said no. Do not try again unless they ask you to.")
        if answer == "session" and allow_session:
            self._session_until = self.clock() + SESSION_SECONDS
        self.log(f"USER APPROVED ({answer}): {description}")
