"""Scripted stand-in for unclick.pipeline.run (UNCLICK_FAKE=1).

Emits a realistic ~75 s event sequence covering every event kind, so the UI can be
built and demoed before the real pipeline is wired up. UNCLICK_FAKE_SPEED=4 runs it 4x faster.
"""

from __future__ import annotations

import base64
import io
import json
import os
import random
import threading
import time
from typing import Any

from unclick.contracts import EventSink, PublishedApi, RunResult

SITE_HOST = "projects.dev"
BACKEND_HOST = "api.projects.dev"
BACKEND_URL = f"https://{BACKEND_HOST}/v1/leaderboard?limit=20&sort=votes"
API_SANDBOX = "sb-api-7f3a9c"
API_URL = f"https://{API_SANDBOX}-8000.sb.createos.sh/v1/run"

PROJECTS = [
    ("Unclick", 412, "unclick"),
    ("Ledgerline", 377, "ledgerline"),
    ("PaperTrail AI", 341, "papertrail-ai"),
    ("Sandcastle", 318, "sandcastle"),
    ("MeterMaid", 296, "metermaid"),
    ("Forklift", 271, "forklift"),
    ("Invoicely Agent", 255, "invoicely-agent"),
    ("Quiet Hours", 231, "quiet-hours"),
    ("Tabula", 214, "tabula"),
    ("Pocket SRE", 198, "pocket-sre"),
]

SAMPLE_OUTPUT: list[dict[str, Any]] = [
    {"rank": i + 1, "name": n, "votes": v, "url": f"https://projects.dev/p/{slug}"}
    for i, (n, v, slug) in enumerate(PROJECTS)
]

_lock = threading.Lock()
_calls_remaining = 100


def _speed() -> float:
    try:
        return max(0.1, float(os.getenv("UNCLICK_FAKE_SPEED", "1")))
    except ValueError:
        return 1.0


def _sleep(s: float) -> None:
    time.sleep(s / _speed())


# ---------------------------------------------------------------------------
# Placeholder screenshots (Pillow if present, else a flat PNG)
# ---------------------------------------------------------------------------


def _png(frame: int, highlight: int | None = None, scroll: int = 0, banner: str | None = None) -> str:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return _flat_png()

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (14, 17, 23))
    d = ImageDraw.Draw(img)

    def font(size: int):
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except Exception:  # noqa: BLE001
                continue
        return ImageFont.load_default()

    f_sm, f_md, f_lg = font(15), font(19), font(30)
    # browser chrome
    d.rectangle([0, 0, W, 76], fill=(32, 33, 36))
    d.rounded_rectangle([12, 8, 260, 36], 8, fill=(53, 54, 58))
    d.text((26, 13), "Leaderboard · projects.dev", font=f_sm, fill=(230, 230, 230))
    d.rounded_rectangle([90, 42, W - 20, 70], 14, fill=(53, 54, 58))
    d.text((112, 47), "projects.dev/leaderboard", font=f_sm, fill=(220, 220, 220))
    for i, c in enumerate([(237, 106, 94), (245, 191, 79), (97, 197, 84)]):
        d.ellipse([20 + i * 20, 48, 32 + i * 20, 60], fill=c)
    # page
    d.rectangle([0, 76, W, H], fill=(250, 250, 249))
    d.text((80, 104), "Leaderboard", font=f_lg, fill=(17, 17, 17))
    d.text((80, 146), "Top projects from the CreateOS x Stripe hackathon", font=f_md, fill=(100, 100, 100))
    y0 = 196 - scroll
    for i, (name, votes, _) in enumerate(PROJECTS):
        y = y0 + i * 58
        if y < 180 or y > H - 40:
            continue
        fill = (237, 242, 255) if highlight == i else (255, 255, 255)
        d.rounded_rectangle([80, y, W - 80, y + 48], 10, fill=fill, outline=(225, 225, 225))
        d.text((100, y + 13), f"#{i + 1}", font=f_md, fill=(140, 140, 140))
        d.text((160, y + 13), name, font=f_md, fill=(17, 17, 17))
        d.rounded_rectangle([W - 220, y + 9, W - 100, y + 39], 8, fill=(17, 17, 17))
        d.text((W - 196, y + 14), f"▲ {votes}", font=f_md, fill=(255, 255, 255))
    if banner:
        d.rounded_rectangle([W - 470, H - 70, W - 20, H - 20], 10, fill=(17, 24, 39))
        d.text((W - 450, H - 57), banner, font=f_sm, fill=(134, 239, 172))
    # cursor
    cx, cy = 300 + (frame * 137) % 700, 230 + (frame * 89) % 380
    d.polygon([(cx, cy), (cx, cy + 22), (cx + 6, cy + 16), (cx + 15, cy + 16)], fill=(0, 0, 0), outline=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _flat_png() -> str:
    import struct
    import zlib

    w, h = 320, 180
    raw = b"".join(b"\x00" + bytes((24, 28, 38)) * w for _ in range(h))

    def chunk(t: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


# ---------------------------------------------------------------------------
# Scripted code attempts
# ---------------------------------------------------------------------------

CODE_1 = '''"""Unclick client: projects.dev leaderboard -> JSON."""
import json
import sys

import httpx

API = "https://api.projects.dev/v1/leaderboard"
HEADERS = {"accept": "application/json", "user-agent": "unclick/0.1"}


def fetch(limit: int = 20) -> list[dict]:
    r = httpx.get(API, params={"limit": limit, "sort": "votes"},
                  headers=HEADERS, timeout=20)
    r.raise_for_status()
    rows = r.json()["projects"]
    return [
        {"name": p["name"], "votes": p["votes"], "url": p["url"]}
        for p in rows
    ]


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print(json.dumps(fetch(limit), indent=2))
'''

CODE_2 = '''"""Unclick client: projects.dev leaderboard -> JSON."""
import json
import sys

import httpx

API = "https://api.projects.dev/v1/leaderboard"
HEADERS = {"accept": "application/json", "user-agent": "unclick/0.1"}


def fetch(limit: int = 20) -> list[dict]:
    r = httpx.get(API, params={"limit": limit, "sort": "votes"},
                  headers=HEADERS, timeout=20)
    r.raise_for_status()
    rows = r.json()["projects"]
    return [
        {
            "rank": i + 1,
            "name": p["name"],
            "votes": p["voteCount"],
            "url": f"https://projects.dev/p/{p['slug']}",
        }
        for i, p in enumerate(rows)
    ]


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print(json.dumps(fetch(limit), indent=2))
'''

TRACEBACK_1 = """Traceback (most recent call last):
  File "/workspace/client.py", line 25, in <module>
    print(json.dumps(fetch(limit), indent=2))
                     ^^^^^^^^^^^^
  File "/workspace/client.py", line 17, in fetch
    {"name": p["name"], "votes": p["votes"], "url": p["url"]}
                                 ~^^^^^^^^^
KeyError: 'votes'
"""


def _expected() -> str:
    return "\n".join(f"#{i + 1}  {n}  ▲ {v}" for i, (n, v, _) in enumerate(PROJECTS[:5]))


def _got(rows: list[dict]) -> str:
    return "\n".join(f"#{r['rank']}  {r['name']}  ▲ {r['votes']}" for r in rows[:5])


# ---------------------------------------------------------------------------
# The scripted run
# ---------------------------------------------------------------------------


def run(url: str, task: str, sink: EventSink) -> RunResult:
    global _calls_remaining
    with _lock:
        _calls_remaining = 0
    e = sink.emit
    frame = 0

    def shot(**kw: Any) -> None:
        nonlocal frame
        frame += 1
        e("screenshot", "Desktop screenshot", png_b64=_png(frame, **kw))

    # --- boot -------------------------------------------------------------
    e("status", "Booting the CreateOS desktop sandbox", phase="boot")
    e("log", f"Task: {task}", level="info")
    _sleep(2.5)
    e("sandbox", "Desktop sandbox running (desktop:1, 4 vCPU)", id="sb-desk-41c2e8", role="desktop", action="created", ms=4210.0)
    e("egress", f"Egress locked to {SITE_HOST}", allow=[SITE_HOST], added=[SITE_HOST], reason="Only the target site is reachable during exploration")
    _sleep(1.2)
    e("live_view", "Live desktop view ready", url="/fake-desktop.html")
    shot()
    _sleep(1.5)
    e("sandbox", "Runner sandbox running (devbox:1)", id="sb-run-9b07d1", role="runner", action="created", ms=1830.0)

    # --- explore ------------------------------------------------------------
    e("status", "Exploring the site in Chrome", phase="explore")
    _sleep(1.0)
    e("browser_action", f"Open {url}", action="open", detail=url)
    _sleep(2.0)
    shot()
    e("capture", "JSON response captured", url=f"https://{SITE_HOST}/_next/data/build-8f2c/leaderboard.json", method="GET", status=200, content_type="application/json", size=2140)
    _sleep(1.5)
    e("browser_action", "Wait for the leaderboard to render", action="wait", detail="table rows visible")
    _sleep(1.5)
    e("capture", "JSON response captured", url=f"https://{SITE_HOST}/api/session", method="GET", status=200, content_type="application/json", size=318)
    shot(highlight=0)
    _sleep(2.0)
    e("browser_action", "Scroll through the list", action="scroll", detail="dy=600")
    _sleep(1.0)
    shot(scroll=120)
    e("capture", "JSON response captured", url=BACKEND_URL, method="GET", status=200, content_type="application/json", size=18432)
    _sleep(2.0)
    e("browser_action", "Click 'Sort by votes'", action="click", detail="button 'Votes' at (1062, 164)")
    _sleep(1.5)
    shot(highlight=1, scroll=60)
    e("capture", "JSON response captured", url=BACKEND_URL.replace("limit=20", "limit=20&cursor=0"), method="GET", status=200, content_type="application/json", size=18440)
    _sleep(1.5)
    e("browser_action", "Type in the search box", action="type", detail="'agent'")
    _sleep(1.0)
    e("browser_action", "Press Enter", action="press", detail="Enter")
    e("capture", "JSON response captured", url=f"https://{BACKEND_HOST}/v1/search?q=agent", method="GET", status=200, content_type="application/json", size=4096)
    shot(highlight=2)
    _sleep(2.0)

    # --- discover -------------------------------------------------------------
    e("status", "Discovering the hidden API", phase="discover")
    e("log", "6 JSON responses captured; ranking candidates against the visible rows", level="info")
    _sleep(2.5)
    e("endpoint", "Picked the hidden leaderboard API", url=BACKEND_URL, method="GET",
      why="Returns all 20 visible rows with name, voteCount and slug; no auth or CSRF needed")
    _sleep(1.0)
    e("egress", f"Egress: + {BACKEND_HOST}", allow=[SITE_HOST, BACKEND_HOST], added=[BACKEND_HOST], reason="The client needs the hidden backend host")
    _sleep(2.0)

    # --- code attempt 1 ----------------------------------------------------------
    e("status", "Writing the Python client", phase="code")
    _sleep(2.0)
    e("code", "Attempt 1: client.py", attempt=1, filename="client.py", code=CODE_1)
    _sleep(2.5)
    e("log", "Running client.py in the runner sandbox", level="info")
    _sleep(1.5)
    e("run", "Attempt 1 failed (exit 1)", attempt=1, exit_code=1, stdout="", stderr=TRACEBACK_1, ms=812.4)
    _sleep(1.0)
    e("status", "Verifying output against the screen", phase="verify")
    e("verify", "Attempt 1 does not match the screen", attempt=1, ok=False, expected=_expected(),
      got="(no output: KeyError: 'votes')",
      notes="Response items use 'voteCount' and 'slug', not 'votes' and 'url'.")
    _sleep(2.5)
    e("log", "Reading the traceback: KeyError 'votes' → items expose voteCount; build url from slug", level="warn")
    _sleep(2.0)

    # --- code attempt 2 ----------------------------------------------------------
    e("status", "Fixing the client", phase="code")
    _sleep(1.5)
    e("code", "Attempt 2: client.py (fix KeyError)", attempt=2, filename="client.py", code=CODE_2)
    _sleep(2.5)
    out = json.dumps(SAMPLE_OUTPUT, indent=2)
    e("run", "Attempt 2 succeeded (exit 0)", attempt=2, exit_code=0, stdout=out, stderr="", ms=644.9)
    _sleep(1.0)
    e("status", "Verifying output against the screen", phase="verify")
    _sleep(1.5)
    e("verify", "Output matches the screen", attempt=2, ok=True, expected=_expected(), got=_got(SAMPLE_OUTPUT),
      notes="20/20 rows match the screen (name, votes, url).")
    _sleep(2.0)

    # --- publish ------------------------------------------------------------------
    e("status", "Publishing the API", phase="publish")
    e("sandbox", "Pausing the verified runner sandbox", id="sb-run-9b07d1", role="runner", action="pausing")
    _sleep(1.5)
    e("sandbox", "Runner paused (memory + disk snapshot)", id="sb-run-9b07d1", role="runner", action="paused", ms=1420.0)
    _sleep(1.0)
    e("sandbox", "Forked API sandbox from the verified runner", id=API_SANDBOX, role="api", action="forked", forked_from="sb-run-9b07d1", ms=2380.0)
    e("egress", "API egress: backend + Stripe only", allow=[BACKEND_HOST, "api.stripe.com"], added=["api.stripe.com"],
      reason="Published API can only reach the backend and Stripe")
    _sleep(1.5)
    e("sandbox", "Desktop sandbox destroyed", id="sb-desk-41c2e8", role="desktop", action="destroyed")
    curl = f"curl -s '{API_URL}?limit=10' -H 'X-API-Key: uk_test_4f9c2a7e'"
    e("publish", "API is live on the sandbox's public URL", api_url=API_URL,
      docs_url=API_URL.replace("/v1/run", "/docs"), curl=curl, sandbox_id=API_SANDBOX)
    _sleep(1.5)
    e("billing", "Stripe Checkout session created (test mode)", event="checkout_created",
      checkout_url="https://checkout.stripe.com/c/pay/cs_test_a1B2c3D4")
    _sleep(2.0)
    with _lock:
        _calls_remaining = 100
    e("billing", "Payment received: 100 calls ($1.00, test mode)", event="paid", api_key="uk_test_4f9c2a7e",
      calls_remaining=100, amount_cents=100)
    _sleep(1.5)
    e("status", "API is live", phase="live")
    _sleep(1.0)
    e("log", "curl → 200 OK in 284 ms (no LLM in the hot path)", level="info")
    charge_call(sink)
    _sleep(2.5)

    # --- auto-pause + wake ---------------------------------------------------------
    e("sandbox", "API sandbox idle → auto-pausing", id=API_SANDBOX, role="api", action="pausing")
    _sleep(1.5)
    e("sandbox", "API sandbox paused", id=API_SANDBOX, role="api", action="paused", ms=1210.0)
    e("pause", "API sandbox paused (auto_pause_after_seconds=60)", state="paused")
    _sleep(3.5)
    e("pause", "Incoming request: waking the sandbox", state="waking")
    _sleep(1.0)
    e("sandbox", "API sandbox resumed", id=API_SANDBOX, role="api", action="resumed", ms=842.0)
    e("pause", "Awake in 842 ms", state="awake", wake_ms=842.0)
    charge_call(sink)
    _sleep(1.0)
    e("done", "Unclicked: projects.dev leaderboard is now an API", summary="2 attempts, 1 fix, published and billed", api_url=API_URL)

    api = PublishedApi(api_url=API_URL, docs_url=API_URL.replace("/v1/run", "/docs"), curl=curl, sandbox_id=API_SANDBOX)
    return RunResult(ok=True, endpoint=BACKEND_URL, client_code=CODE_2, sample_output=SAMPLE_OUTPUT, attempts=2, api=api)


def charge_call(sink: EventSink) -> None:
    global _calls_remaining
    with _lock:
        _calls_remaining = max(0, _calls_remaining - 1)
        left = _calls_remaining
    sink.emit("billing", "API call charged: 1 call (test mode)", event="call_charged", calls_remaining=left, amount_cents=1)


def fake_purchase(sink: EventSink) -> None:
    global _calls_remaining
    with _lock:
        _calls_remaining += 100
        left = _calls_remaining
    sink.emit("billing", "Stripe Checkout session created (test mode)", event="checkout_created",
              checkout_url="https://checkout.stripe.com/c/pay/cs_test_fake")
    sink.emit("billing", "Payment received: 100 calls ($1.00, test mode)", event="paid", api_key="uk_test_4f9c2a7e",
              calls_remaining=left, amount_cents=100)


def fake_wake(sink: EventSink | None) -> dict[str, Any]:
    if sink:
        sink.emit("sandbox", "API sandbox paused", id=API_SANDBOX, role="api", action="paused")
        sink.emit("pause", "API sandbox paused", state="paused")
    time.sleep(0.6)
    if sink:
        sink.emit("pause", "Incoming request: waking the sandbox", state="waking")
    ms = random.uniform(650, 980)
    time.sleep(ms / 1000)
    if sink:
        sink.emit("sandbox", "API sandbox resumed", id=API_SANDBOX, role="api", action="resumed", ms=round(ms, 1))
        sink.emit("pause", f"Awake in {ms:.0f} ms", state="awake", wake_ms=round(ms, 1))
        charge_call(sink)
    return {"wake_ms": round(ms, 1), "status": 200}
