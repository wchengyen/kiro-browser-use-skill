#!/usr/bin/env python3
"""
browser_step.py — one browser action per invocation, driven by Kiro (no LLM key).

Kiro is the "brain": it reads the JSON state this script prints, decides the next
action, and calls this script again. A single Chrome instance is kept alive across
calls via CDP (remote debugging on a fixed port), so state persists between steps.

Commands:
  start                     Launch a persistent headless Chrome (CDP :9222).
  state                     Print current url/title + indexed interactive elements.
  goto <url>                Navigate, then print state.
  click <index>             Click the element with that index, then print state.
  type <index> <text...>    Type text into the element, then print state.
  scroll <up|down> [amount] Scroll the page, then print state.
  screenshot [path]         Save a PNG screenshot; print its path.
  stop                      Kill the persistent Chrome and clean up.

All human/library logging goes to stderr; stdout is ONLY a single JSON object.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

# Keep browser-use's own logging off stdout so stdout stays pure JSON.
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "error")

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
RUN_DIR = "/tmp/kiro_browser"
PID_FILE = os.path.join(RUN_DIR, "chrome.pid")
PROFILE_DIR = os.path.join(RUN_DIR, "profile")

CHROME_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/snap/bin/chromium",
]


def _emit(obj: dict) -> None:
    """Print exactly one JSON object to stdout (the channel Kiro reads)."""
    print(json.dumps(obj, ensure_ascii=False))


def _find_chrome() -> str | None:
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


def _cdp_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def cmd_start() -> dict:
    if _cdp_ready():
        return {"ok": True, "status": "already_running", "cdp_url": CDP_URL}

    chrome = _find_chrome()
    if not chrome:
        return {"ok": False, "error": "No Chrome/Chromium binary found."}

    os.makedirs(PROFILE_DIR, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={PROFILE_DIR}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--no-sandbox",
        "--window-size=1280,900",
        "about:blank",
    ]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach: survives this process exiting
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    for _ in range(50):  # wait up to ~10s for CDP to come up
        if _cdp_ready():
            return {"ok": True, "status": "started", "pid": proc.pid, "cdp_url": CDP_URL}
        time.sleep(0.2)
    return {"ok": False, "error": "Chrome launched but CDP did not become ready."}


def cmd_stop() -> dict:
    killed = []
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed.append(pid)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except Exception:
                pass
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    return {"ok": True, "status": "stopped", "killed_pids": killed}


async def _with_session(action):
    """Connect to the persistent Chrome, run `action(session)`, disconnect cleanly."""
    from browser_use import BrowserSession

    if not _cdp_ready():
        return {"ok": False, "error": "Browser not running. Run `start` first."}

    session = BrowserSession(cdp_url=CDP_URL, is_local=False, keep_alive=True)
    await session.start()
    try:
        return await action(session)
    finally:
        # keep_alive=True => disconnect without killing the external Chrome
        try:
            await session.stop()
        except Exception:
            pass


async def _state_payload(session) -> dict:
    """url + title + indexed interactive elements (builds the selector map)."""
    # get_browser_state_summary populates the selector map used by clicks/types.
    await session.get_browser_state_summary(include_screenshot=False)
    elements_text = await session.get_state_as_text()
    return {
        "ok": True,
        "url": await session.get_current_page_url(),
        "title": await session.get_current_page_title(),
        "elements": elements_text,
    }


async def act_state():
    return await _with_session(_state_payload)


async def act_goto(url: str):
    from browser_use.browser.events import NavigateToUrlEvent

    if "://" not in url:
        url = "https://" + url

    async def _run(session):
        # Dispatch directly (instead of navigate_to) so we can give the first,
        # slow cold-start navigation a generous timeout and wait only for the DOM.
        event = session.event_bus.dispatch(
            NavigateToUrlEvent(url=url, wait_until="domcontentloaded", event_timeout=60.0)
        )
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await _state_payload(session)

    return await _with_session(_run)


async def act_click(index: int):
    from browser_use.browser.events import ClickElementEvent

    async def _run(session):
        await session.get_browser_state_summary(include_screenshot=False)
        node = await session.get_element_by_index(index)
        if node is None:
            return {"ok": False, "error": f"No element with index {index}."}
        event = session.event_bus.dispatch(ClickElementEvent(node=node))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        await asyncio.sleep(0.5)
        payload = await _state_payload(session)
        payload["action"] = f"clicked index {index}"
        return payload

    return await _with_session(_run)


async def act_type(index: int, text: str):
    from browser_use.browser.events import TypeTextEvent

    async def _run(session):
        await session.get_browser_state_summary(include_screenshot=False)
        node = await session.get_element_by_index(index)
        if node is None:
            return {"ok": False, "error": f"No element with index {index}."}
        event = session.event_bus.dispatch(TypeTextEvent(node=node, text=text, clear=True))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        await asyncio.sleep(0.3)
        payload = await _state_payload(session)
        payload["action"] = f"typed into index {index}"
        return payload

    return await _with_session(_run)


async def act_search(target: str, text: str):
    """Type into a field and press Enter — both in one session, so the volatile
    submit-button index never matters (robust for dynamic search pages).
    `target` may be a numeric index or a stable "#element_id" selector."""
    from browser_use.browser.events import TypeTextEvent, SendKeysEvent

    async def _run(session):
        await session.get_browser_state_summary(include_screenshot=False)
        if target.startswith("#"):
            index = await session.get_index_by_id(target[1:])
            if index is None:
                return {"ok": False, "error": f"No element with id {target[1:]}."}
        else:
            index = int(target)
        node = await session.get_element_by_index(index)
        if node is None:
            return {"ok": False, "error": f"No element for target {target}."}
        ev = session.event_bus.dispatch(TypeTextEvent(node=node, text=text, clear=True))
        await ev
        await ev.event_result(raise_if_any=True, raise_if_none=False)
        ev = session.event_bus.dispatch(SendKeysEvent(keys="Enter"))
        await ev
        await ev.event_result(raise_if_any=True, raise_if_none=False)
        await asyncio.sleep(2.0)  # let results load
        payload = await _state_payload(session)
        payload["action"] = f"searched '{text}' via {target}"
        return payload

    return await _with_session(_run)


async def act_scroll(direction: str, amount: int):
    from browser_use.browser.events import ScrollEvent

    async def _run(session):
        event = session.event_bus.dispatch(ScrollEvent(direction=direction, amount=amount))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        await asyncio.sleep(0.3)
        payload = await _state_payload(session)
        payload["action"] = f"scrolled {direction} {amount}"
        return payload

    return await _with_session(_run)


async def act_screenshot(path: str):
    async def _run(session):
        data = await session.take_screenshot(path=path)
        size = len(data) if isinstance(data, (bytes, bytearray)) else None
        return {"ok": True, "screenshot": path, "bytes": size}

    return await _with_session(_run)


def main(argv: list[str]) -> int:
    if not argv:
        _emit({"ok": False, "error": "No command. See --help in the source header."})
        return 2

    cmd, rest = argv[0], argv[1:]

    if cmd == "start":
        _emit(cmd_start())
        return 0
    if cmd == "stop":
        _emit(cmd_stop())
        return 0

    try:
        if cmd == "state":
            result = asyncio.run(act_state())
        elif cmd == "goto":
            result = asyncio.run(act_goto(rest[0]))
        elif cmd == "click":
            result = asyncio.run(act_click(int(rest[0])))
        elif cmd == "type":
            result = asyncio.run(act_type(int(rest[0]), " ".join(rest[1:])))
        elif cmd == "search":
            result = asyncio.run(act_search(rest[0], " ".join(rest[1:])))
        elif cmd == "scroll":
            amount = int(rest[1]) if len(rest) > 1 else 500
            result = asyncio.run(act_scroll(rest[0], amount))
        elif cmd == "screenshot":
            path = rest[0] if rest else os.path.join(RUN_DIR, "shot.png")
            result = asyncio.run(act_screenshot(path))
        else:
            result = {"ok": False, "error": f"Unknown command: {cmd}"}
    except IndexError:
        result = {"ok": False, "error": f"Missing argument for command: {cmd}"}
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    _emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
