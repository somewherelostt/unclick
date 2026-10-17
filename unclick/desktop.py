"""Desktop (desktop:1) sandbox: Chrome with CDP + an in-sandbox network recorder.

Usage:
    from unclick.contracts import PrintSink
    from unclick.desktop import create_desktop
    from unclick.sandbox import default_factory

    d = create_desktop(default_factory(), ["projects.dev", "*.projects.dev"], PrintSink())
    try:
        d.open("https://projects.dev/leaderboard")
        print(d.live_view_url(), d.page_text()[:500])
        for c in d.captured_requests():
            print(c.seq, c.method, c.status, c.url)
    finally:
        d.sandbox.destroy()
"""

from __future__ import annotations

import json
import os
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unclick.contracts import CapturedRequest, EventSink
from unclick.sandbox import RestSandbox, SandboxError

ASSETS_DIR = Path(__file__).parent / "sandbox_assets"
REMOTE_DIR = "/tmp/unclick"
REMOTE_ASSETS = f"{REMOTE_DIR}/assets"
CAPTURES = f"{REMOTE_DIR}/captures.jsonl"
READY = f"{REMOTE_DIR}/recorder.ready"
CDP_PORT = 9222
PAGE_TEXT_MAX = 20_000

# Free plan caps at 1 vCPU / 1 GB; bigger shapes are tried first and fall back on a 4xx.
DEFAULT_SHAPES = [s for s in [os.getenv("UNCLICK_DESKTOP_SHAPE"), "s-2vcpu-4gb", "s-1vcpu-2gb", "s-1vcpu-1gb"] if s]

CHROME_SH = r"""#!/bin/sh
# Launch Chrome on the sandbox's X display with CDP enabled.
CHROME=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || ls /opt/google/chrome/chrome 2>/dev/null)
if [ -z "$DISPLAY" ]; then
  DISPLAY=$(ps -eo args | grep -oE '^[^ ]*(Xvfb|Xvnc|Xtigervnc|Xorg|vncserver)[^ ]* :[0-9]+' | grep -oE ':[0-9]+' | head -1)
  export DISPLAY="${DISPLAY:-:0}"
fi
AUTH=$(ps -eo args | grep -E '(Xvfb|Xvnc|Xtigervnc|Xorg)' | grep -v grep | grep -oE '\-auth [^ ]+' | head -1 | cut -d' ' -f2)
[ -n "$AUTH" ] && [ -z "$XAUTHORITY" ] && export XAUTHORITY="$AUTH"
mkdir -p /tmp/unclick/chrome
echo "chrome=$CHROME display=$DISPLAY xauth=$XAUTHORITY user=$(id -un)"
exec "$CHROME" --remote-debugging-port=__PORT__ --remote-debugging-address=127.0.0.1 --remote-allow-origins=* \
  --user-data-dir=/tmp/unclick/chrome --no-first-run --no-default-browser-check --disable-dev-shm-usage \
  --no-sandbox --test-type --password-store=basic --disable-features=Translate,ChromeWhatsNew,PrivacySandboxSettings4 \
  --disable-search-engine-choice-screen --window-position=0,0 --window-size=__W__,__H__ --start-maximized about:blank
"""


class SandboxDesktop:
    """Implements contracts.Desktop on top of a RestSandbox running desktop:1."""

    def __init__(self, sandbox: RestSandbox, sink: EventSink | None = None):
        self.sandbox = sandbox
        self.sink = sink
        self.python = "python3"
        self.screen_size = (1280, 800)
        self.display = ""
        self.chrome_pid: str | None = None
        self.recorder_pid: str | None = None
        self.probe: dict[str, Any] = {}
        self._live: dict | None = None

    # -- low-level computer API ---------------------------------------------------
    def _computer(self, method: str, path: str, json_body: Any = None, raw: bool = False,
                  params: dict | None = None, timeout: float = 60) -> Any:
        data, _ = self.sandbox._http.request(
            method, f"/v1/sandboxes/{self.sandbox.id}/computer{path}", json=json_body,
            params=params, raw=raw, timeout=timeout)
        return data

    def _log(self, msg: str, **data: Any) -> None:
        if self.sink:
            self.sink.emit("log", msg, level="info", **data)

    # -- setup -------------------------------------------------------------------
    def wait_for_desktop(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                scr = self._computer("GET", "/screen", timeout=15) or {}
                if scr.get("width"):
                    self.screen_size = (int(scr["width"]), int(scr["height"]))
                return
            except SandboxError as e:
                last = e
                if e.status_code == 501:
                    raise
                time.sleep(1.0)
        raise SandboxError(f"desktop not ready after {timeout}s: {last}")

    def upload_assets(self) -> None:
        for name in ("cdpws.py", "recorder.py", "cdp.py"):
            self.sandbox.write_file(f"{REMOTE_ASSETS}/{name}", (ASSETS_DIR / name).read_bytes())
        script = (CHROME_SH.replace("__PORT__", str(CDP_PORT))
                  .replace("__W__", str(self.screen_size[0])).replace("__H__", str(self.screen_size[1])))
        self.sandbox.write_file(f"{REMOTE_ASSETS}/chrome.sh", script)
        self.sandbox.exec(f"chmod +x {REMOTE_ASSETS}/chrome.sh; : > {CAPTURES}; rm -f {READY}")

    def run_probe(self) -> dict[str, Any]:
        """Records what the image actually has (useful for debugging, printed by smoke_desktop)."""
        r = self.sandbox.exec(
            "echo user=$(id -un); echo python3=$(command -v python3); python3 --version 2>&1;"
            "echo node=$(command -v node);"
            "echo chrome=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser);"
            "python3 -c 'import websocket' 2>/dev/null && echo websocket-client=yes || echo websocket-client=no;"
            "python3 -c 'import websockets' 2>/dev/null && echo websockets=yes || echo websockets=no;"
            "echo xservers=$(ps -eo args | grep -E 'Xvfb|Xvnc|Xtigervnc|Xorg' | grep -v grep | head -2 | tr '\\n' '|');"
            "echo chrome_running=$(pgrep -c -f 'chrome' || true)",
            timeout=30)
        probe: dict[str, Any] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                probe[k.strip()] = v.strip()
            elif line.startswith("Python"):
                probe["python_version"] = line.strip()
        try:
            screens = self._computer("GET", "/screens") or []
            if isinstance(screens, dict):
                screens = screens.get("screens") or screens.get("data") or []
            probe["screens"] = screens
            for s in screens:
                if s.get("screen_id") in ("screen-0", None) and s.get("display"):
                    d = str(s["display"])
                    self.display = d if d.startswith(":") else f":{d}"
                    break
        except SandboxError as e:
            probe["screens_error"] = str(e)[:300]
        self.probe = probe
        if not probe.get("python3"):
            raise SandboxError("desktop image has no python3; recorder cannot run", body=probe)
        return probe

    def start_chrome(self, timeout: int = 60) -> None:
        env = {"DISPLAY": self.display} if self.display else None
        cmd = f"{REMOTE_ASSETS}/chrome.sh > {REMOTE_DIR}/chrome.log 2>&1"
        if env:
            cmd = f"DISPLAY={shlex.quote(self.display)} {cmd}"
        self.chrome_pid = self.sandbox.start_process(cmd)
        r = self.sandbox.exec(
            f"for i in $(seq 1 {timeout * 2}); do curl -s --max-time 1 http://127.0.0.1:{CDP_PORT}/json/version"
            f" && exit 0; sleep 0.5; done; exit 1", timeout=timeout + 15)
        if r.exit_code != 0:
            log = self.sandbox.exec(f"tail -c 3000 {REMOTE_DIR}/chrome.log").stdout
            raise SandboxError("Chrome CDP did not come up on port 9222", body=log)
        self.probe["cdp_version"] = r.stdout.strip()[:400]
        self._maximize_chrome()

    def _maximize_chrome(self) -> None:
        try:
            wins = self._computer("GET", "/windows") or []
            if isinstance(wins, dict):
                wins = wins.get("windows") or []
            for w in wins:
                if "chrom" in str(w.get("title", "")).lower() or "about:blank" in str(w.get("title", "")):
                    self._computer("POST", f"/windows/{w['id']}/maximize")
                    self._computer("POST", f"/windows/{w['id']}/focus")
                    break
        except (SandboxError, KeyError, TypeError):
            pass

    def start_recorder(self, timeout: int = 30) -> None:
        self.recorder_pid = self.sandbox.start_process(
            f"{self.python} -u {REMOTE_ASSETS}/recorder.py --port {CDP_PORT} --out {CAPTURES} --ready {READY}"
            f" > {REMOTE_DIR}/recorder.log 2>&1")
        r = self.sandbox.exec(
            f"for i in $(seq 1 {timeout * 2}); do [ -f {READY} ] && exit 0; sleep 0.5; done; exit 1",
            timeout=timeout + 10)
        if r.exit_code != 0:
            log = self.sandbox.exec(f"tail -c 3000 {REMOTE_DIR}/recorder.log").stdout
            raise SandboxError("network recorder did not become ready", body=log)

    def recorder_log(self, n_bytes: int = 4000) -> str:
        return self.sandbox.exec(f"tail -c {n_bytes} {REMOTE_DIR}/recorder.log 2>&1").stdout

    # -- CDP helpers -------------------------------------------------------------
    def _cdp(self, *args: str, timeout: int = 45, env: str = "") -> Any:
        cmd = f"{env} {self.python} {REMOTE_ASSETS}/cdp.py " + " ".join(shlex.quote(a) for a in args)
        r = self.sandbox.exec(cmd.strip(), timeout=timeout)
        if r.exit_code != 0:
            raise SandboxError(f"cdp.py {args[0]} failed (exit {r.exit_code})", body=r.stderr[-1500:])
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else {}

    def eval_js(self, expression: str) -> Any:
        return self._cdp("eval", expression).get("value")

    def current_url(self) -> str:
        return self._cdp("info").get("url", "")

    def select_option(self, css: str, value_or_label: str) -> str:
        """Set a <select> by value or visible label and fire input/change (React-safe). Returns the new value."""
        js = (
            "(() => { const s = document.querySelector(%s); if (!s) return null;"
            " const want = %s; const o = [...s.options].find(o => o.value === want || o.text.trim() === want);"
            " if (!o) return null;"
            " Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, o.value);"
            " s.dispatchEvent(new Event('input', {bubbles: true}));"
            " s.dispatchEvent(new Event('change', {bubbles: true})); return s.value; })()"
        ) % (json.dumps(css), json.dumps(value_or_label))
        val = self.eval_js(js)
        if val is None:
            raise SandboxError(f"select {css!r} or option {value_or_label!r} not found")
        return val

    def wait_for_capture(self, url_contains: str = "", since_seq: int = 0, timeout: float = 15,
                         json_only: bool = True) -> list[CapturedRequest]:
        """Poll the recorder until a capture (seq > since_seq) whose URL contains url_contains appears."""
        deadline = time.monotonic() + timeout
        while True:
            caps = [c for c in self.captured_requests(since_seq, json_only) if url_contains in c.url]
            if caps or time.monotonic() > deadline:
                return caps
            time.sleep(1.0)

    def last_seq(self) -> int:
        r = self.sandbox.exec(f"wc -l < {CAPTURES} 2>/dev/null || echo 0")
        try:
            return int(r.stdout.strip() or 0)
        except ValueError:
            return 0

    # -- Desktop protocol ----------------------------------------------------------
    def live_view_url(self, refresh: bool = False) -> str:
        if self._live and not refresh and not _expired(self._live.get("expires_at")):
            return self._live["url"]
        data = self._computer("GET", "/screens/screen-0/connect") or {}
        url = data.get("url")
        if not url:
            base = self.sandbox.preview_url(int(data["port"]))
            path = data.get("path") or "/vnc.html"
            if not path.startswith("/"):
                path = "/" + path
            url = base + path
            if data.get("token") and data["token"] not in url:
                url += ("&" if "?" in url else "?") + "token=" + str(data["token"])
        data["url"] = url
        self._live = data
        return url

    def open(self, url: str, settle: float = 1.5) -> None:
        try:
            self._cdp("navigate", url, timeout=60, env=f"SETTLE={settle}")
        except (SandboxError, ValueError) as e:
            self._log(f"CDP navigate failed, falling back to computer/open: {e}")
            self._computer("POST", "/open", {"target": url})
            time.sleep(settle + 2)

    def screenshot(self) -> bytes:
        return self._computer("GET", "/screenshot", raw=True, timeout=60)

    def click(self, x: int, y: int) -> None:
        self._computer("POST", "/mouse/click", {"x": int(x), "y": int(y), "button": "left"})

    def type(self, text: str) -> None:
        self._computer("POST", "/keyboard/type", {"text": text, "delay_in_ms": 15}, timeout=120)

    def press(self, keys: list[str]) -> None:
        self._computer("POST", "/keyboard/press", {"keys": list(keys)})

    def scroll(self, dy: int) -> None:
        """dy in pixels (positive = down); ~100 px per wheel notch."""
        direction = "down" if dy >= 0 else "up"
        amount = max(1, round(abs(dy) / 100))
        x, y = self.screen_size[0] // 2, self.screen_size[1] // 2
        try:
            self._computer("POST", "/mouse/move", {"x": x, "y": y})
        except SandboxError:
            pass
        self._computer("POST", "/mouse/scroll", {"direction": direction, "amount": amount})

    def page_text(self) -> str:
        text = self.eval_js("document.body ? document.body.innerText : ''") or ""
        return text[:PAGE_TEXT_MAX]

    def captured_requests(self, since_seq: int = 0, json_only: bool = True) -> list[CapturedRequest]:
        """Captures with seq > since_seq (seq starts at 1)."""
        try:
            raw = self.sandbox.read_file(CAPTURES)
        except SandboxError as e:
            if e.status_code == 404:
                return []
            raise
        out: list[CapturedRequest] = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # partial last line
            if rec.get("seq", 0) <= since_seq:
                continue
            if json_only and not _is_jsonish(rec.get("content_type", ""), rec.get("response_body", "")):
                continue
            out.append(CapturedRequest(
                url=rec.get("url", ""), method=rec.get("method", "GET"), status=int(rec.get("status", 0)),
                content_type=rec.get("content_type", ""), request_headers=rec.get("request_headers") or {},
                request_body=rec.get("request_body"), response_headers=rec.get("response_headers") or {},
                response_body=rec.get("response_body") or "", seq=int(rec.get("seq", 0)),
            ))
        return out


def _is_jsonish(content_type: str, body: str) -> bool:
    if "json" in (content_type or "").lower():
        return True
    head = (body or "").lstrip()[:1]
    return head in ("{", "[")


def _expired(expires_at: Any) -> bool:
    if not expires_at:
        return False
    try:
        t = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        return t <= datetime.now(timezone.utc)
    except ValueError:
        return False


def create_desktop(factory, egress: list[str] | None, sink: EventSink | None = None,
                   shapes: list[str] | None = None) -> SandboxDesktop:
    """Boot desktop:1 (ingress on), launch Chrome with CDP + recorder; emits `sandbox` and `live_view`."""
    t0 = time.perf_counter()
    sb = None
    last: SandboxError | None = None
    for shape in shapes or DEFAULT_SHAPES:
        try:
            sb = factory.create(rootfs="desktop:1", shape=shape, egress=egress or None, ingress=True,
                                name=None)
            break
        except SandboxError as e:
            last = e
            if e.status_code in (400, 402, 403, 422):  # shape not allowed on plan -> try smaller
                continue
            raise
    if sb is None:
        raise last or SandboxError("could not create desktop sandbox")
    ms = (time.perf_counter() - t0) * 1000
    if sink:
        sink.emit("sandbox", f"Desktop sandbox {sb.id} created ({sb.info.get('shape', '')})",
                  id=sb.id, role="desktop", action="created", ms=round(ms, 1))
    d = SandboxDesktop(sb, sink)
    try:
        d.wait_for_desktop()
        d.upload_assets()
        d.run_probe()
        d._log("Launching Chrome with DevTools protocol")
        d.start_chrome()
        d.start_recorder()
        try:
            url = d.live_view_url()
            if sink:
                sink.emit("live_view", "Live view ready", url=url)
        except SandboxError as e:
            if sink:
                sink.emit("log", f"live view unavailable: {e}", level="warn")
    except Exception:
        sb.destroy()
        if sink:
            sink.emit("sandbox", f"Desktop sandbox {sb.id} destroyed after setup failure",
                      id=sb.id, role="desktop", action="destroyed")
        raise
    return d
