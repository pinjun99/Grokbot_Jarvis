import json

import anyio
import httpx
from mcp.client.client import Client

from .conftest import KEY


def call(h, tool, args=None):
    async def go():
        async with Client(h.url) as client:
            return await client.call_tool(tool, args or {})
    return anyio.run(go)


def text(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


def test_lists_all_tools(harness):
    async def go():
        async with Client(harness.url) as client:
            return [t.name for t in (await client.list_tools()).tools]
    names = anyio.run(go)
    for expected in ["screenshot", "click", "type_text", "open_app", "open_url", "press_keys",
                     "run_shell", "run_applescript", "ask_user_permission", "read_screen_text", "speak"]:
        assert expected in names


def test_screenshot_returns_image(harness):
    result = call(harness, "screenshot")
    kinds = [c.type for c in result.content]
    assert kinds == ["image", "text"]
    assert result.content[0].mime_type == "image/jpeg"
    assert "1280x800" in text(result)


def test_open_app_and_click_run_without_asking_in_assist(harness):
    assert not call(harness, "open_app", {"name": "Safari"}).is_error
    assert not call(harness, "click", {"x": 10, "y": 20}).is_error
    assert ("open_app", "Safari") in harness.mac.calls
    assert ("click", 10, 20, "left", 1) in harness.mac.calls
    assert harness.prompts == []


def test_shell_asks_and_respects_no(harness):
    result = call(harness, "run_shell", {"command": "ls"})
    assert result.is_error
    assert "said no" in text(result)
    assert harness.mac.calls == []
    assert len(harness.prompts) == 1


def test_shell_runs_when_user_allows(harness):
    harness.answers = ["once"]
    result = call(harness, "run_shell", {"command": "echo hi"})
    assert not result.is_error
    assert json.loads(text(result))["stdout"] == "hi\n"


def test_watch_mode_blocks_actions_but_allows_looking(harness):
    harness.mode = "watch"
    assert not call(harness, "screenshot").is_error
    result = call(harness, "click", {"x": 1, "y": 1})
    assert result.is_error and "watch" in text(result)
    assert harness.mac.calls == []


def test_pause_blocks_everything(harness):
    harness.paused = True
    assert call(harness, "open_app", {"name": "Safari"}).is_error
    assert harness.mac.calls == []


def test_ask_user_permission_always_pops_up(harness):
    harness.mode = "autopilot"
    harness.answers = ["once"]
    result = call(harness, "ask_user_permission", {"action_summary": "Send email to Bob"})
    assert not result.is_error
    message, allow_session = harness.prompts[0]
    assert "Send email to Bob" in message and allow_session is False


def test_status_reports_mode(harness):
    harness.mode = "ask"
    status = json.loads(text(call(harness, "get_status")))
    assert status["mode"] == "ask" and status["frontmost_app"] == "Safari"


def test_wrong_key_is_rejected(harness):
    assert httpx.post(f"{harness.base}/wrong-key/mcp", json={}).status_code == 404
    assert httpx.post(f"{harness.base}/mcp", json={}).status_code == 404
    assert httpx.get(harness.base).text == "Jarvis is online."


def _rpc(url, method, params, msg_id=1, headers=None):
    return httpx.post(
        url,
        json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
        headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18",
                 **(headers or {})},
    )


def test_older_protocol_client_like_grok(harness):
    """Grok's servers may speak an older MCP version; check the plain JSON-RPC handshake works."""
    init = _rpc(harness.url, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "grok", "version": "1"},
    })
    assert init.status_code == 200, init.text
    body = init.json()
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert "Jarvis" in body["result"]["instructions"]

    listed = _rpc(harness.url, "tools/list", {}, 2)
    assert listed.status_code == 200, listed.text
    assert any(t["name"] == "click" for t in listed.json()["result"]["tools"])


def test_bearer_header_on_plain_mcp_path(harness):
    init = _rpc(f"{harness.base}/mcp", "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "grok", "version": "1"},
    }, headers={"Authorization": f"Bearer {KEY}"})
    assert init.status_code == 200, init.text
