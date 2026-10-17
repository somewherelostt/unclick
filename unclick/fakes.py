"""Offline fakes for the sandbox layer and the agent, so the full pipeline runs without CreateOS or an LLM.

- FakeSandbox: exec runs locally via subprocess in a temp dir (sandbox paths under /tmp/unclick map there).
- FakeDesktop: serves a canned leaderboard over a local HTTP server and returns canned captures + page text.
- ScriptedAgent: deterministic stand-in for agent.Agent (used only when no OPENAI_API_KEY or --scripted).
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from unclick.agent import ExploreState, ToolCall
from unclick.contracts import CapturedRequest, ExecResult

WORKDIR = "/tmp/unclick"
LIVE_API = "https://projects.dev/api/leaderboard"
FILTERS = ["All Projects", "Vercel Ship NYC 2026", "CreateOS 2026"]

LEADERBOARD = {"projects": [
    {"id": "p1", "project_name": "Rescue511", "project_url": "https://www.rescue511.com", "user_name": "corey511",
     "upvotes": 107, "description": "Real-time incident intelligence for towing and emergency services"},
    {"id": "p2", "project_name": "Sugarlock", "project_url": "https://sugarlock.vercel.app", "user_name": "dana",
     "upvotes": 88, "description": "Glucose-aware meal planner"},
    {"id": "p3", "project_name": "Stacksmith", "project_url": "https://stacksmith-seven.vercel.app", "user_name": "lee",
     "upvotes": 64, "description": "Generate a full stack from a sentence"},
    {"id": "p4", "project_name": "CashOps", "project_url": "https://stripecashops.lovable.app", "user_name": "sam",
     "upvotes": 51, "description": "Treasury dashboard on Stripe"},
    {"id": "p5", "project_name": "Athena Agent", "project_url": "https://stripe-hackathon-athena-agent.vercel.app",
     "user_name": "ari", "upvotes": 37, "description": "Agentic finance assistant"},
]}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/leaderboard"):
            body = json.dumps(LEADERBOARD).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):  # silence
        pass


_server: ThreadingHTTPServer | None = None


def fake_site_base() -> str:
    """Start (once) a local HTTP server that plays the site's hidden backend."""
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_server.server_address[1]}"


def _tiny_png(w: int = 640, h: int = 400) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return len(d).to_bytes(4, "big") + t + d + zlib.crc32(t + d).to_bytes(4, "big")
    raw = b"".join(b"\x00" + b"\x20\x20\x28" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


_ABS_PATH = re.compile(r"(?<![\w.:/-])(/(?:tmp|srv|home|opt|root|var|workspace)(?:/[^\s'\";&|<>]*)?)")
_SECRET_ENV = re.compile(r"KEY|SECRET|TOKEN|PASSWORD", re.I)


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Host env minus secrets: code run "in the sandbox" (generated client, API server) never sees our keys."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    return {**env, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(extra or {})}


class FakeSandbox:
    """A "sandbox" that is a temp dir + local processes. Works on Windows: the shell lines the pipeline and
    publish send (bash syntax: tail, curl loops, pkill, exec ... >> log) are emulated in Python."""

    def __init__(self, role: str = "runner", egress: list[str] | None = None, files_from: str | None = None,
                 prefix: str = "sb-fake"):
        self.id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        self.prefix = prefix
        self.role = role
        self.root = tempfile.mkdtemp(prefix="unclick-fake-")
        if files_from:
            shutil.copytree(files_from, self.root, dirs_exist_ok=True)
        self._egress = list(egress or [])
        self._status = "running"
        self.ingress = False
        self.auto_pause = 0
        self._procs: dict[str, subprocess.Popen] = {}
        self._proc_cmds: dict[str, str] = {}
        self._ports: dict[int, int] = {}  # sandbox port -> host port (many local APIs can all "use" 8000)

    def _local(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/").replace("/", os.sep))

    def host_port(self, port: int) -> int:
        if port not in self._ports:
            self._ports[port] = _free_port()
        return self._ports[port]

    def _translate(self, cmd: str) -> str:
        cmd = _ABS_PATH.sub(lambda m: f'"{self._local(m.group(1))}"' if m.group(1) != "/dev/null" else m.group(1), cmd)
        if os.name == "nt":
            cmd = cmd.replace("2>/dev/null", "2>NUL").replace(">/dev/null", ">NUL")
            cmd = re.sub(r"\bcd (\")", r"cd /d \1", cmd)
        return re.sub(r"\bpython3\b", lambda _: f'"{sys.executable}"', cmd)

    def _emulate(self, cmd: str, timeout: int) -> tuple[str, str, int] | None:
        """Python versions of the bash one-liners publish.py sends (Windows has no tail/seq/pkill)."""
        m = re.fullmatch(r"\s*tail -n (\d+) (\S+)( 2>&1)?\s*", cmd)
        if m:
            try:
                with open(self._local(m.group(2)), encoding="utf-8", errors="replace") as f:
                    return "".join(f.readlines()[-int(m.group(1)):]), "", 0
            except OSError as e:
                return "", f"tail: cannot open {m.group(2)}: {e.strerror}", 1
        m = re.search(r"curl [^\n]*?https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0):(\d+)(/[^\s'\";&|]*)", cmd)
        if m and "seq" in cmd:  # health-wait loop
            import urllib.request
            url = f"http://127.0.0.1:{self.host_port(int(m.group(1)))}{m.group(2)}"
            deadline = time.monotonic() + min(timeout, 45)
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=2) as r:
                        return r.read().decode("utf-8", "replace"), "", 0
                except Exception:  # noqa: BLE001
                    time.sleep(0.5)
            return "", "health check timed out", 1
        if "pkill" in cmd or cmd.lstrip().startswith("chmod"):
            m = re.search(r"pkill -f '([^']+)'", cmd)
            if m:
                pat = m.group(1).replace("[", "").replace("]", "")
                for pid, c in list(self._proc_cmds.items()):
                    if pat in c and self._procs[pid].poll() is None:
                        self._procs[pid].kill()
            return "", "", 0
        return None

    def exec(self, cmd: str, timeout: int = 120, env: dict[str, str] | None = None) -> ExecResult:
        os.makedirs(self._local(WORKDIR), exist_ok=True)
        t0 = time.perf_counter()
        emulated = self._emulate(cmd, timeout)
        if emulated is not None:
            out_s, err_s, code = emulated
            return ExecResult(out_s, err_s, code, (time.perf_counter() - t0) * 1000)
        try:
            p = subprocess.run(self._translate(cmd), shell=True, capture_output=True, timeout=timeout,
                               cwd=self.root, env=_clean_env(env))
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            out, err, code = e.stdout or b"", (e.stderr or b"") + b"\ntimeout", 124
        return ExecResult(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), code,
                          (time.perf_counter() - t0) * 1000)

    def start_process(self, cmd: str) -> str:
        """`cd X && exec python3 -u /path/server.py >> /tmp/log 2>&1` -> Popen([python, -u, local server.py]) with
        stdout/stderr appended to the local log and PORT set to this sandbox's host port for 8000."""
        pid = f"proc_{uuid.uuid4().hex[:6]}"
        m = re.search(r"python3?\s+(?:-u\s+)?(\S+\.py)", cmd)
        if m:
            script = self._local(m.group(1)) if m.group(1).startswith("/") else os.path.join(self.root, m.group(1))
            lm = re.search(r">>?\s*(\S+)", cmd[m.end():])
            log_path = self._local(lm.group(1)) if lm else os.path.join(self.root, f"{pid}.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_f = open(log_path, "ab")  # noqa: SIM115  (kept open for the child's lifetime)
            port_env = {"PORT": str(self.host_port(8000))}
            self._procs[pid] = subprocess.Popen([sys.executable, "-u", script], cwd=os.path.dirname(script),
                                                stdout=log_f, stderr=subprocess.STDOUT, env=_clean_env(port_env))
        else:
            self._procs[pid] = subprocess.Popen(self._translate(cmd), shell=True, cwd=self.root, env=_clean_env())
        self._proc_cmds[pid] = cmd
        return pid

    def write_file(self, path: str, content: str | bytes) -> None:
        p = self._local(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content.encode() if isinstance(content, str) else content)

    def read_file(self, path: str) -> bytes:
        with open(self._local(path), "rb") as f:
            return f.read()

    def get_egress(self) -> list[str]:
        return list(self._egress)

    def set_egress(self, allow: list[str]) -> None:
        self._egress = list(allow)

    def enable_ingress(self) -> None:
        self.ingress = True

    def preview_url(self, port: int) -> str:
        return f"http://127.0.0.1:{self.host_port(port)}"

    def set_auto_pause(self, seconds: int) -> None:
        self.auto_pause = seconds

    def status(self) -> str:
        return self._status

    def pause(self) -> None:
        self._status = "paused"

    def resume(self) -> None:
        self._status = "running"

    def fork(self, egress: list[str] | None = None, ingress: bool | None = None,
             envs: dict[str, str] | None = None) -> "FakeSandbox":
        if self._status != "paused":
            raise RuntimeError("fork: source must be paused")
        child = FakeSandbox(role="api", egress=self._egress if egress is None else egress, files_from=self.root,
                            prefix=self.prefix)
        child.ingress = bool(ingress)
        if self.on_fork:
            self.on_fork(child)
        return child

    on_fork = None  # set by FakeFactory so forked children are findable by id

    def destroy(self) -> None:
        for p in self._procs.values():
            try:
                if os.name == "nt" and p.poll() is None:  # whole tree (Chrome helpers, shell children)
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=15)
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        self._status = "destroyed"
        shutil.rmtree(self.root, ignore_errors=True)


class FakeFactory:
    def __init__(self, prefix: str = "sb-fake"):
        self.prefix = prefix
        self.created: list[FakeSandbox] = []

    def _track(self, sb: FakeSandbox) -> None:
        sb.on_fork = self._track
        self.created.append(sb)

    def create(self, *, rootfs: str = "devbox:1", shape: str = "s-1vcpu-1gb", egress: list[str] | None = None,
               ingress: bool = False, envs: dict[str, str] | None = None, name: str | None = None) -> FakeSandbox:
        sb = FakeSandbox(role="desktop" if rootfs.startswith("desktop") else "runner", egress=egress,
                         prefix=self.prefix)
        sb.ingress = ingress
        self._track(sb)
        return sb

    def get(self, sandbox_id: str) -> FakeSandbox | None:
        return next((s for s in self.created if s.id == sandbox_id), None)


class FakeDesktop:
    """Pretends Chrome loaded the page: page text from the canned data, captures incl. decoys."""

    def __init__(self, sandbox: FakeSandbox):
        self.sandbox = sandbox
        self.url = ""
        self._seq = 0
        self._captures: list[CapturedRequest] = []
        self._live: dict | None = None
        self._filter: str | None = None  # None = untouched, else the selected option

    def live_view_url(self) -> str:
        return f"https://fake-novnc.local/{self.sandbox.id}/vnc.html"

    def _cap(self, url: str, body: Any, ct: str = "application/json", status: int = 200) -> None:
        self._seq += 1
        text = body if isinstance(body, str) else json.dumps(body)
        self._captures.append(CapturedRequest(
            url=url, method="GET", status=status, content_type=ct,
            request_headers={"accept": "*/*", "referer": self.url, "user-agent": "Mozilla/5.0 Chrome/130"},
            request_body=None, response_headers={"content-type": ct}, response_body=text, seq=self._seq))

    def open(self, url: str) -> None:
        # Like projects.dev: the list is server-rendered, so loading the page only fires auth/flags/analytics JSON.
        self.url = url
        base = fake_site_base()
        self._cap(f"{base}/api/flags", {"enableNewUI": True, "experiments": ["a", "b"]})
        self._cap("https://clerk.projects.dev/v1/environment", {"auth_config": {"single_session_mode": True}})
        self._cap("https://projects.dev/leaderboard/_vercel/insights/view", {"ok": True})

    def _refetch(self, query: str = "") -> None:
        """Any interaction with the filter re-fetches the data from the hidden API.
        UNCLICK_FAKE_LIVE=1 captures the real https://projects.dev/api/leaderboard instead of the canned copy."""
        if os.getenv("UNCLICK_FAKE_LIVE") == "1":
            import requests
            r = requests.get(LIVE_API + query, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                             timeout=20)
            if not query:
                self._live = r.json()
            self._cap(LIVE_API + query, r.text)
        else:
            self._cap(f"{fake_site_base()}/api/leaderboard{query}", LEADERBOARD)

    def eval_js(self, expr: str) -> Any:
        """Emulates the pipeline's CDP helpers for the hackathon <select> (controls, select_option, restore check)."""
        if "unclickIdx = String" in expr:  # CONTROLS_JS
            cur = self._filter or FILTERS[0]
            return [{"i": 0, "tag": "button", "text": "Submit Your Project", "x": 1100, "y": 120},
                    {"i": 1, "tag": "select", "text": f"Filter by hackathon: [current: {cur}] options: "
                     + " | ".join(FILTERS), "x": 300, "y": 330}]
        if "dispatchEvent" in expr:  # SELECT_JS(idx, option)
            m = re.search(r"\((\d+), (\".*\")\)\s*$", expr)
            idx, want = int(m.group(1)), json.loads(m.group(2))
            if idx != 1:
                return f"error: control {idx} is not a dropdown"
            opt = next((f for f in FILTERS if want.lower() in f.lower()), None)
            if not opt:
                return f"error: no option matching {want}"
            self._filter = opt
            self._refetch("" if opt == FILTERS[0] else "?hackathon=" + re.sub(r"\W+", "-", opt.lower()))
            return f"selected {opt}"
        if "unclick-orig" in expr:  # CHANGED_JS
            return [] if self._filter in (None, FILTERS[0]) else ["dropdown #1: set it back to 'All Projects'"]
        return None

    def screenshot(self) -> bytes:
        return _tiny_png()

    def click(self, x: int, y: int) -> None:
        pass

    def type(self, text: str) -> None:
        pass

    def press(self, keys: list[str]) -> None:
        if "Return" in keys:
            self._refetch()

    def scroll(self, dy: int) -> None:
        pass

    def page_text(self) -> str:
        data = self._live or LEADERBOARD
        if os.getenv("UNCLICK_FAKE_LIVE") == "1" and not self._live:
            import requests
            data = self._live = requests.get(LIVE_API, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
        rows = [f"{i}\n{p['project_name']}\n{p.get('description') or ''}\n@{p['user_name']}\nRepo\nStack\n{p['upvotes']}"
                for i, p in enumerate(data["projects"][:25], 1)]
        return "STRIPE PROJECTS\nLEADERBOARD\nDiscover, upvote, and share the projects developers have built.\n" \
               "Rank\nProject\nLinks\nVotes\n" + "\n".join(rows)

    def captured_requests(self, since_seq: int = 0, json_only: bool = True) -> list[CapturedRequest]:
        return [c for c in self._captures if c.seq > since_seq and (not json_only or "json" in c.content_type)]


def fake_desktop_factory(sandbox: FakeSandbox) -> FakeDesktop:
    return FakeDesktop(sandbox)


# ---------------------------------------------------------------------------
# Local sandbox mode: a real Chrome on this machine instead of a CreateOS desktop:1
# ---------------------------------------------------------------------------

CHROME_PATHS = [
    os.getenv("UNCLICK_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130.0.0.0 Safari/537.36")
_KEYS = {  # xdotool-style names the agent uses -> (key, code, windowsVirtualKeyCode)
    "return": ("Enter", "Enter", 13), "enter": ("Enter", "Enter", 13), "tab": ("Tab", "Tab", 9),
    "escape": ("Escape", "Escape", 27), "esc": ("Escape", "Escape", 27), "space": (" ", "Space", 32),
    "backspace": ("Backspace", "Backspace", 8), "delete": ("Delete", "Delete", 46),
    "down": ("ArrowDown", "ArrowDown", 40), "up": ("ArrowUp", "ArrowUp", 38),
    "left": ("ArrowLeft", "ArrowLeft", 37), "right": ("ArrowRight", "ArrowRight", 39),
    "home": ("Home", "Home", 36), "end": ("End", "End", 35),
    "page_down": ("PageDown", "PageDown", 34), "pagedown": ("PageDown", "PageDown", 34), "next": ("PageDown", "PageDown", 34),
    "page_up": ("PageUp", "PageUp", 33), "pageup": ("PageUp", "PageUp", 33), "prior": ("PageUp", "PageUp", 33),
}
_MODS = {"ctrl": 2, "control": 2, "alt": 1, "shift": 8, "meta": 4, "cmd": 4, "super": 4}


def find_chrome() -> str | None:
    for p in CHROME_PATHS:
        if not p:
            continue
        if os.path.isabs(p) and os.path.exists(p):
            return p
        if not os.path.isabs(p) and shutil.which(p):
            return shutil.which(p)
    return None


class LocalChromeDesktop:
    """Desktop protocol on a local Chrome over CDP: real page, real screenshots, real network capture.

    UNCLICK_LOCAL_HEADLESS=0 shows the Chrome window. No egress enforcement and no noVNC live view (local mode).
    """

    def __init__(self, sandbox: FakeSandbox, width: int = 1366, height: int = 900):
        from websockets.sync.client import connect

        self.sandbox = sandbox
        self.width, self.height = width, height
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("Chrome not found (set UNCLICK_CHROME to chrome.exe)")
        port = _free_port()
        self.profile = tempfile.mkdtemp(prefix="unclick-chrome-")
        args = [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--mute-audio",
                f"--window-size={width},{height}", "--remote-allow-origins=*", "about:blank"]
        if os.getenv("UNCLICK_LOCAL_HEADLESS", "1") != "0":
            args.insert(1, "--headless=new")
            args.insert(2, "--hide-scrollbars")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sandbox._procs[f"chrome_{port}"] = self.proc  # sandbox.destroy() kills Chrome
        sandbox._proc_cmds[f"chrome_{port}"] = "chrome"
        ws_url = self._page_ws(port)
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=15)
        self._send_lock = threading.Lock()
        self._next = 0
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._reqs: dict[str, dict] = {}
        self._captures: list[CapturedRequest] = []
        self._seq = 0
        self._loaded = threading.Event()
        self._bodies: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._reader, daemon=True, name="cdp-reader").start()
        threading.Thread(target=self._body_worker, daemon=True, name="cdp-bodies").start()
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable", {"maxResourceBufferSize": 20_000_000, "maxTotalBufferSize": 100_000_000})
        self.call("Network.setUserAgentOverride", {"userAgent": CHROME_UA, "acceptLanguage": "en-US,en"})
        self.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                         "deviceScaleFactor": 1, "mobile": False})
        self.url = ""

    @staticmethod
    def _page_ws(port: int) -> str:
        import urllib.request
        deadline = time.monotonic() + 20
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    for t in json.loads(r.read().decode()):
                        if t.get("type") == "page":
                            return t["webSocketDebuggerUrl"]
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(0.3)
        raise RuntimeError(f"Chrome DevTools did not come up on port {port}: {last}")

    # -- CDP plumbing ------------------------------------------------------
    def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        with self._send_lock:
            self._next += 1
            mid = self._next
            ev, box = threading.Event(), {}
            self._pending[mid] = (ev, box)
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not ev.wait(timeout):
            self._pending.pop(mid, None)
            raise TimeoutError(f"CDP {method} timed out")
        if "error" in box:
            raise RuntimeError(f"CDP {method}: {box['error'].get('message')}")
        return box.get("result", {})

    def _reader(self) -> None:
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001  (socket closed: Chrome killed)
                for ev, box in list(self._pending.values()):
                    box["error"] = {"message": "Chrome connection closed"}
                    ev.set()
                return
            if "id" in msg:
                ev_box = self._pending.pop(msg["id"], None)
                if ev_box:
                    ev_box[1].update(msg)
                    ev_box[0].set()
                continue
            m, p = msg.get("method"), msg.get("params", {})
            if m == "Network.requestWillBeSent":
                r = p.get("request", {})
                self._reqs[p["requestId"]] = {"url": r.get("url", ""), "method": r.get("method", "GET"),
                                              "headers": r.get("headers", {}), "body": r.get("postData"),
                                              "type": p.get("type", "")}
            elif m == "Network.responseReceived":
                info = self._reqs.setdefault(p["requestId"], {"url": p["response"].get("url", ""), "method": "GET",
                                                              "headers": {}, "body": None})
                resp = p.get("response", {})
                info.update(status=resp.get("status", 0), mime=resp.get("mimeType", ""),
                            resp_headers={k.lower(): v for k, v in (resp.get("headers") or {}).items()},
                            type=p.get("type", info.get("type", "")))
            elif m == "Network.loadingFinished":
                info = self._reqs.get(p["requestId"])
                if info and (info.get("type") in ("XHR", "Fetch") or "json" in (info.get("mime") or "")):
                    self._bodies.put(p["requestId"])
            elif m == "Page.loadEventFired":
                self._loaded.set()

    def _body_worker(self) -> None:
        import base64
        while True:
            rid = self._bodies.get()
            if rid is None:
                return
            info = self._reqs.get(rid) or {}
            try:
                res = self.call("Network.getResponseBody", {"requestId": rid}, timeout=15)
                body = res.get("body", "")
                if res.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            self._seq += 1
            self._captures.append(CapturedRequest(
                url=info.get("url", ""), method=info.get("method", "GET"), status=int(info.get("status") or 0),
                content_type=(info.get("resp_headers") or {}).get("content-type", info.get("mime", "")),
                request_headers={k.lower(): v for k, v in (info.get("headers") or {}).items()},
                request_body=info.get("body"), response_headers=info.get("resp_headers") or {},
                response_body=body[:200_000], seq=self._seq))

    # -- Desktop protocol --------------------------------------------------
    def live_view_url(self) -> str:
        raise RuntimeError("local sandbox mode: no noVNC live view, showing Chrome screenshots instead")

    def open(self, url: str) -> None:
        self.url = url
        self._loaded.clear()
        self.call("Page.navigate", {"url": url}, timeout=30)
        self._loaded.wait(20)
        time.sleep(1.0)  # let client-side fetches settle

    def screenshot(self) -> bytes:
        import base64
        res = self.call("Page.captureScreenshot", {"format": "png"}, timeout=20)
        return base64.b64decode(res["data"])

    def eval_js(self, expr: str) -> Any:
        res = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True},
                        timeout=20)
        if res.get("exceptionDetails"):
            raise RuntimeError(str(res["exceptionDetails"].get("text") or res["exceptionDetails"])[:300])
        return (res.get("result") or {}).get("value")

    def click(self, x: int, y: int) -> None:
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left",
                                                   "clickCount": 0 if t == "mouseMoved" else 1})

    def type(self, text: str) -> None:
        self.call("Input.insertText", {"text": text})

    def press(self, keys: list[str]) -> None:
        mods = 0
        main: list[str] = []
        for k in keys:
            if k.lower() in _MODS:
                mods |= _MODS[k.lower()]
            else:
                main.append(k)
        for k in main:
            key, code, vk = _KEYS.get(k.lower(), (k, f"Key{k.upper()}" if len(k) == 1 else k, ord(k.upper()[0])))
            base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": mods}
            text = {"text": "\r" if key == "Enter" else key} if (len(key) == 1 or key == "Enter") and not mods & 6 else {}
            self.call("Input.dispatchKeyEvent", {"type": "keyDown", **base, **text})
            self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def scroll(self, dy: int) -> None:
        self.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": self.width // 2, "y": self.height // 2,
                                               "deltaX": 0, "deltaY": dy})

    def page_text(self) -> str:
        return (self.eval_js("document.body ? document.body.innerText : ''") or "")[:20_000]

    def captured_requests(self, since_seq: int = 0, json_only: bool = True) -> list[CapturedRequest]:
        return [c for c in list(self._captures) if c.seq > since_seq
                and (not json_only or "json" in (c.content_type or "").lower())]


def local_desktop_factory(sandbox: FakeSandbox) -> Any:
    """Local sandbox mode desktop: real local Chrome; UNCLICK_LOCAL_DESKTOP=fake (or no Chrome) uses the
    FakeDesktop against the live projects.dev API instead."""
    if os.getenv("UNCLICK_LOCAL_DESKTOP", "chrome") != "fake" and find_chrome():
        return LocalChromeDesktop(sandbox)
    os.environ.setdefault("UNCLICK_FAKE_LIVE", "1")
    return FakeDesktop(sandbox)


class ScriptedAgent:
    """Deterministic agent for offline tests. Its first client has a real bug (wrong JSON key) to exercise the fix loop."""

    model = "scripted"

    def explore_start(self, url: str, task: str) -> ExploreState:
        return ExploreState()

    def explore_next(self, state: ExploreState, *, step: int, captures: list[str], **_: Any) -> ToolCall:
        script = [ToolCall("done", {"summary": "The leaderboard is visible."}),  # rejected: no data JSON yet
                  ToolCall("click", {"x": 900, "y": 260, "label": "hackathon filter"}),
                  ToolCall("press", {"keys": ["Down"]}), ToolCall("press", {"keys": ["Return"]}),
                  ToolCall("click", {"x": 900, "y": 260, "label": "hackathon filter"}),
                  ToolCall("press", {"keys": ["Home"]}), ToolCall("press", {"keys": ["Return"]})]
        if step <= len(script):
            return script[step - 1]
        return ToolCall("done", {"summary": "Leaderboard is visible and its JSON was captured."})

    def explore_result(self, state: ExploreState, call: ToolCall, result: str) -> None:
        pass

    def pick_endpoint(self, task: str, page_text: str, candidates: list[str]) -> tuple[int, str]:
        for i, c in enumerate(candidates):
            if "leaderboard" in c and "insights" not in c:
                return i, "Its response lists every project name and vote count shown on screen."
        return 0, "Best heuristic match."

    def extract_expected(self, task: str, page_text: str) -> list[dict[str, Any]]:
        return [{"name": p["project_name"], "votes": p["upvotes"]} for p in LEADERBOARD["projects"][:3]]

    def write_client(self, ctx: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        return self._code(ctx["url"], "votes"), [{"url": ctx["url"]}]

    def fix_client(self, conv: list[dict[str, Any]], feedback: dict[str, Any]) -> str:
        return self._code(conv[0]["url"], "upvotes")

    @staticmethod
    def _code(url: str, votes_key: str) -> str:
        return f'''import json
import requests

URL = {url!r}


def run(limit: int = 50, **params):
    r = requests.get(URL, headers={{"Accept": "application/json", "User-Agent": "Mozilla/5.0"}}, timeout=20)
    r.raise_for_status()
    rows = r.json()["projects"]
    return [{{"name": p["project_name"], "votes": p["{votes_key}"], "url": p.get("project_url")}} for p in rows][:limit]


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
'''
