"""Checks every MacController action on a real Mac. Run it from Terminal.app:

    uv run python scripts/mac_smoke_test.py

It opens a new TextEdit document and a Safari tab, types, clicks, scrolls and drags in
them, and closes them again without saving. Don't touch the mouse or keyboard until it
asks you to. Results go to the screen and to ~/.jarvis/smoke-test.log.
"""

from __future__ import annotations

import sys
import time
import traceback

from jarvis import config
from jarvis.mac import MacController, run_osascript

LOG = config.HOME / "smoke-test.log"
SHOT = config.HOME / "smoke-test.jpg"
results: list[str] = []


def report(line: str) -> None:
    print(line, flush=True)
    results.append(line)


def check(name: str, fn) -> object:
    try:
        value = fn()
        report(f"PASS  {name}" + (f"  ->  {value}" if value is not None else ""))
        return value
    except Exception as e:  # report every failure and keep going
        report(f"FAIL  {name}  ->  {e}")
        results.append(traceback.format_exc())
        return None


def textedit(script: str) -> str:
    return run_osascript(f'tell application "TextEdit"\n{script}\nend tell')


def mouse_points() -> tuple[float, float]:
    import Quartz

    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return round(loc.x, 1), round(loc.y, 1)


def find_text(mac: MacController, word: str) -> dict:
    for item in mac.read_screen_text():
        if word.lower() in item["text"].lower():  # TextEdit capitalises the start of each line
            return item
    raise RuntimeError(f"OCR could not find {word!r} on screen")


def main() -> int:
    mac = MacController(max_width=config.load()["screenshot_width"])
    saved_clipboard = mac.get_clipboard()
    report(f"Screen: {mac.screen_size_points()} points, screenshots {mac.screenshot_size()}, scale {mac.scale:.3f}")
    check("permissions", mac.permissions)

    def screenshot():
        shot = mac.screenshot()
        SHOT.write_bytes(shot.data)
        return f"{shot.width}x{shot.height}, saved to {SHOT}"

    check("screenshot()", screenshot)
    check("read_screen_text()", lambda: f"{len(mac.read_screen_text())} pieces of text")

    def mouse_lines_up():
        w, h = mac.screenshot_size()
        x, y = w * 0.25, h * 0.5
        mac.move_mouse(x, y)
        time.sleep(0.2)
        got = mouse_points()
        want = (round(x / mac.scale, 1), round(y / mac.scale, 1))
        if abs(got[0] - want[0]) > 2 or abs(got[1] - want[1]) > 2:
            raise RuntimeError(f"mouse at {got}, expected {want}")
        return f"screenshot ({x:.0f}, {y:.0f}) -> screen points {got}"

    check("move_mouse() lines up with screenshot", mouse_lines_up)

    # ---- TextEdit: typing, clicking, keys, drag, scroll ----
    mac.open_app("TextEdit")
    time.sleep(1.5)
    textedit("activate\nmake new document\nset bounds of front window to {100, 100, 900, 700}")
    time.sleep(1.0)

    def typing():
        mac.type_text("héllo 👋 Jarvis\nsecond line")
        time.sleep(0.5)
        got = textedit("get text of front document")
        if got.lower() != "héllo 👋 jarvis\nsecond line":  # TextEdit may capitalise "Second"
            raise RuntimeError(f"TextEdit contains {got!r}")
        return repr(got)

    check('type_text("héllo 👋 ...")', typing)

    def click_on_ocr_word():
        mac.press_keys("cmd+a")
        mac.type_text("ZEBRAWORD")
        time.sleep(0.8)
        word = find_text(mac, "ZEBRAWORD")
        mac.set_clipboard("")
        mac.click(word["x"], word["y"], clicks=2)  # double-click selects the word
        time.sleep(0.2)
        mac.press_keys("cmd+c")
        time.sleep(0.3)
        copied = mac.get_clipboard().strip()
        if copied != "ZEBRAWORD":
            raise RuntimeError(f"double-click + cmd+c copied {copied!r} (clicked at {word['x']}, {word['y']})")
        return f"double-clicked ({word['x']}, {word['y']}) and copied {copied!r}"

    check("click() on OCR position + press_keys(cmd+c)", click_on_ocr_word)

    def drag_select():
        mac.press_keys("cmd+a")
        mac.type_text("DRAGSTART middle words DRAGEND")
        time.sleep(0.8)
        line = find_text(mac, "DRAGSTART")
        mac.set_clipboard("")
        # from the middle of the line to well past its end: selects the second half
        mac.drag(line["x"], line["y"], line["x"] + 300, line["y"])
        time.sleep(0.2)
        mac.press_keys("cmd+c")
        time.sleep(0.3)
        copied = mac.get_clipboard().strip()
        if "DRAGEND" not in copied or "DRAGSTART" in copied:
            raise RuntimeError(f"drag selected {copied!r}")
        return f"selected {copied!r}"

    check("drag()", drag_select)

    def scrolling():
        mac.press_keys("cmd+a")
        mac.type_text("\n".join(f"line {n}" for n in range(1, 151)))
        time.sleep(0.5)
        mac.press_keys("cmd+up")
        time.sleep(0.5)
        before = {i["text"] for i in mac.read_screen_text()}
        line = find_text(mac, "line 5")
        mac.scroll("down", 15, line["x"], line["y"])
        time.sleep(0.8)
        after = {i["text"] for i in mac.read_screen_text()}
        if before == after:
            raise RuntimeError("the page did not move")
        return "page moved"

    check("scroll()", scrolling)
    check("close test document", lambda: textedit("close front document saving no") or None)

    # ---- Safari: cmd+t opens a tab ----
    def safari_tab():
        mac.open_app("Safari")
        time.sleep(2)
        count = 'tell application "Safari" to count tabs of front window'
        if run_osascript('tell application "Safari" to count windows') == "0":
            run_osascript('tell application "Safari" to make new document')
            time.sleep(1)
        before = int(run_osascript(count))
        mac.press_keys("cmd+t")
        time.sleep(1)
        after = int(run_osascript(count))
        if after != before + 1:
            raise RuntimeError(f"tabs went {before} -> {after}")
        mac.press_keys("cmd+w")
        return f"tabs {before} -> {after}, then closed it"

    check('press_keys("cmd+t") in Safari', safari_tab)

    # ---- things that need the human ----
    check("running_apps()", lambda: mac.running_apps()["frontmost"])
    check("notify()", lambda: mac.notify("Jarvis test", "If you see this, notifications work") or None)
    print("\nA Jarvis pop-up is about to appear. Please click  'Allow once'.", flush=True)
    answer = check("confirm()", lambda: mac.confirm("Jarvis test: please click 'Allow once'."))
    if answer not in (None, "once"):
        report(f"FAIL  confirm() returned {answer!r}, expected 'once' (did you click Allow once?)")
    check("speak()", lambda: mac.speak("Hello, I am Jarvis. The test is finished.") or None)
    heard = input("\nDid you hear Jarvis speak? (y/n) ").strip().lower()
    report(f"{'PASS' if heard.startswith('y') else 'FAIL'}  speak() heard by owner: {heard!r}")

    mac.set_clipboard(saved_clipboard)
    failed = sum(r.startswith("FAIL") for r in results)
    report(f"\n{'ALL PASSED' if not failed else f'{failed} FAILED'}. Full results: {LOG}")
    LOG.write_text("\n".join(results) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
