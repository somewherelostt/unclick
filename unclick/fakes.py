"""Offline fakes for the sandbox layer and the agent, so the full pipeline runs without CreateOS or an LLM.

- FakeSandbox: exec runs locally via subprocess in a temp dir (sandbox paths under /tmp/unclick map there).
- FakeDesktop: serves a canned leaderboard over a local HTTP server and returns canned captures + page text.
- ScriptedAgent: deterministic stand-in for agent.Agent (used only when no OPENAI_API_KEY or --scripted).
"""

from __future__ import annotations

import json
import os
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


class FakeSandbox:
    def __init__(self, role: str = "runner", egress: list[str] | None = None, files_from: str | None = None):
        self.id = f"sb-fake-{uuid.uuid4().hex[:8]}"
        self.role = role
        self.root = tempfile.mkdtemp(prefix="unclick-fake-")
        if files_from:
            shutil.copytree(files_from, self.root, dirs_exist_ok=True)
        self._egress = list(egress or [])
        self._status = "running"
        self.ingress = False
        self.auto_pause = 0
        self._procs: dict[str, subprocess.Popen] = {}

    def _local(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/").replace("/", os.sep))

    def _translate(self, cmd: str) -> str:
        cmd = cmd.replace(WORKDIR, self._local(WORKDIR))
        return re.sub(r"\bpython3\b", lambda _: f'"{sys.executable}"', cmd)

    def exec(self, cmd: str, timeout: int = 120, env: dict[str, str] | None = None) -> ExecResult:
        os.makedirs(self._local(WORKDIR), exist_ok=True)
        t0 = time.perf_counter()
        full_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(env or {})}
        try:
            p = subprocess.run(self._translate(cmd), shell=True, capture_output=True, timeout=timeout,
                               cwd=self.root, env=full_env)
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            out, err, code = e.stdout or b"", (e.stderr or b"") + b"\ntimeout", 124
        return ExecResult(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), code,
                          (time.perf_counter() - t0) * 1000)

    def start_process(self, cmd: str) -> str:
        pid = f"proc_{uuid.uuid4().hex[:6]}"
        self._procs[pid] = subprocess.Popen(self._translate(cmd), shell=True, cwd=self.root)
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
        return f"http://127.0.0.1:{port}"

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
        child = FakeSandbox(role="api", egress=self._egress if egress is None else egress, files_from=self.root)
        child.ingress = bool(ingress)
        return child

    def destroy(self) -> None:
        for p in self._procs.values():
            p.kill()
        self._status = "destroyed"
        shutil.rmtree(self.root, ignore_errors=True)


class FakeFactory:
    def __init__(self):
        self.created: list[FakeSandbox] = []

    def create(self, *, rootfs: str = "devbox:1", shape: str = "s-1vcpu-1gb", egress: list[str] | None = None,
               ingress: bool = False, envs: dict[str, str] | None = None, name: str | None = None) -> FakeSandbox:
        sb = FakeSandbox(role="desktop" if rootfs.startswith("desktop") else "runner", egress=egress)
        sb.ingress = ingress
        self.created.append(sb)
        return sb


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
