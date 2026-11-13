"""Tiny Chrome DevTools Protocol client for the Jarvis Browser (a dedicated Chrome profile launched with
--remote-debugging-port). Lets Jarvis run JavaScript (e.g. fetch) inside a site Ben is logged in to, without
ever handling his password. Also what Hermes's browser tool attaches to (config browser.cdp_url).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

CDP_URL = os.environ.get("JARVIS_BROWSER_CDP", "http://127.0.0.1:9333").rstrip("/")
PROFILE = Path(os.environ.get("JARVIS_BROWSER_PROFILE") or Path.home() / ".hermes" / "jarvis-browser-chrome")
CHROME_APP = "Google Chrome"


LAUNCHD_LABEL = "ai.jarvis.browser"


def launch_args(start_url: str = "about:blank", visible: bool = False) -> list[str]:
    """Ben's rule: Jarvis never opens windows (the HUD is the only exception). The Jarvis Browser therefore runs
    headless; `visible=True` is only for the one-time sign-in to a site, after which it is hidden again."""
    port = CDP_URL.rsplit(":", 1)[-1]
    common = [f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check"]
    if visible:
        return ["open", "-n", "-a", CHROME_APP, "--args", *common, "--window-size=1280,860", "--window-position=80,60", start_url]
    return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", *common, "--window-size=1280,860", start_url]


def version() -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _uid() -> str:
    return str(os.getuid())


def _kill_profile() -> None:
    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
    for _ in range(40):
        if subprocess.run(["pgrep", "-f", f"user-data-dir={PROFILE}"], capture_output=True).returncode != 0:
            break
        time.sleep(0.25)


def show(start_url: str) -> bool:
    """Bring up a visible Jarvis Browser window (only for signing in). Stops the hidden launchd copy first."""
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{LAUNCHD_LABEL}"], capture_output=True)
    _kill_profile()
    subprocess.Popen(launch_args(start_url, visible=True), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return _wait()


def hide() -> bool:
    """Close any visible window and go back to the hidden copy (launchd if registered, else start it headless)."""
    _kill_profile()
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if plist.exists():
        subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)], capture_output=True)
    else:
        subprocess.Popen(launch_args(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return _wait()


def _wait(wait_s: float = 15) -> bool:
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if version():
            return True
        time.sleep(0.5)
    return False


def ensure_browser(start_url: str = "about:blank", wait_s: float = 15) -> bool:
    """Start the (hidden) Jarvis Browser if its debugging port isn't answering. Returns True when reachable."""
    if version():
        return True
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(launch_args(start_url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if version():
            return True
        time.sleep(0.5)
    return False


class _Session:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.events = ws, 0, {}, asyncio.Queue()

    async def call(self, method: str, params: dict | None = None, session_id: str | None = None, timeout: float = 30) -> dict:
        self.n += 1
        msg: dict[str, Any] = {"id": self.n, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        await self.ws.send(json.dumps(msg))
        t0 = time.time()
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=max(1.0, timeout - (time.time() - t0)))
            d = json.loads(raw)
            if d.get("id") == self.n:
                if "error" in d:
                    raise RuntimeError(f"{method}: {d['error'].get('message')}")
                return d.get("result", {})
            # event; keep going


async def _evaluate(url: str, js: str, timeout: float) -> Any:
    import websockets
    v = version()
    if not v:
        raise RuntimeError("Jarvis Browser is not running (jarvis browser start)")
    async with websockets.connect(v["webSocketDebuggerUrl"], max_size=None) as ws:
        s = _Session(ws)
        tgt = (await s.call("Target.createTarget", {"url": url, "background": True}))["targetId"]
        try:
            sid = (await s.call("Target.attachToTarget", {"targetId": tgt, "flatten": True}))["sessionId"]
            await s.call("Runtime.enable", session_id=sid)
            t0 = time.time()
            while time.time() - t0 < timeout:   # wait for the page to be usable
                r = await s.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True}, session_id=sid)
                if r.get("result", {}).get("value") in ("interactive", "complete"):
                    break
                await asyncio.sleep(0.25)
            r = await s.call("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True, "timeout": int(timeout * 1000)},
                             session_id=sid, timeout=timeout + 5)
            if "exceptionDetails" in r:
                raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"].get("text"))
            return r.get("result", {}).get("value")
        finally:
            try:
                await s.call("Target.closeTarget", {"targetId": tgt})
            except Exception:
                pass


def evaluate(url: str, js: str, timeout: float = 30) -> Any:
    """Open `url` in a background tab of the Jarvis Browser, run `js` (may return a Promise), close the tab."""
    return asyncio.run(_evaluate(url, js, timeout))


def fetch_json(base: str, path: str, timeout: float = 30) -> dict[str, Any]:
    """GET `path` on `base` with the browser's cookies. Returns {"status": int, "body": parsed-json-or-text}."""
    # Run from a small same-origin page that never redirects (robots.txt), with an absolute URL: a page that bounced
    # to the SSO login would otherwise sit on another origin and relative fetches fail to parse.
    js = ("fetch(%s, {credentials: 'include', headers: {'Accept': 'application/json'}})"
          ".then(async r => ({status: r.status, text: await r.text()}))" % json.dumps(base + path))
    out = evaluate(f"{base}/robots.txt", js, timeout) or {}
    text = out.get("text", "")
    try:
        body: Any = json.loads(text)
    except Exception:
        body = text[:4000]
    return {"status": out.get("status", 0), "body": body}


def open_url(url: str) -> None:
    """Open a tab in the (hidden) Jarvis Browser for Jarvis's own work. For something Ben should see, use
    `jarvis mac open <url>` (new tab in his main Chrome) instead."""
    asyncio.run(_open(url))


async def _open(url: str) -> None:
    import websockets
    v = version()
    if not v:
        raise RuntimeError("Jarvis Browser is not running")
    async with websockets.connect(v["webSocketDebuggerUrl"], max_size=None) as ws:
        await _Session(ws).call("Target.createTarget", {"url": url})
