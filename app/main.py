"""Unclick orchestrator web app.

Modes (the header badge always says which one is running):
  uvicorn app.main:app --port 8080                          real CreateOS sandboxes + real agent
  UNCLICK_SANDBOX=local uvicorn app.main:app --port 8080    real agent, local Chrome + local processes (no CreateOS)
  UNCLICK_FAKE=1 uvicorn app.main:app --port 8080           scripted fake pipeline (no LLM, no sandboxes)
  UNCLICK_REPLAY=runs/<name>.jsonl ...                      every new run replays that recording
  /?replay=<name>&speed=2                                   replay runs/<name>.jsonl from the UI

Every run is recorded to runs/<run_id>.jsonl (first line: {"kind": "_meta", ...}).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from unclick.contracts import Event, EventKind  # noqa: E402

log = logging.getLogger("unclick.app")
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Unclick", version="0.1.0")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def fake_mode() -> bool:
    return os.getenv("UNCLICK_FAKE") == "1"


def sandbox_mode() -> str:
    """'local' runs the real pipeline on this machine (unclick.fakes sandboxes + local Chrome)."""
    return "local" if os.getenv("UNCLICK_SANDBOX", "").strip().lower() == "local" else "createos"


RUNS_DIR = Path(os.getenv("UNCLICK_RUNS_DIR", str(ROOT / "runs")))
_LOCAL_FACTORY = None


def local_factory():
    global _LOCAL_FACTORY
    if _LOCAL_FACTORY is None:
        from unclick.fakes import FakeFactory

        _LOCAL_FACTORY = FakeFactory(prefix="sb-local")
        import atexit

        atexit.register(lambda: [sb.destroy() for sb in list(_LOCAL_FACTORY.created)])  # no orphan API servers
    return _LOCAL_FACTORY


def stripe_enabled() -> bool:
    try:
        from unclick import billing  # type: ignore

        return billing.enabled()
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Runs + in-process event sink
# ---------------------------------------------------------------------------


class Run:
    def __init__(self, url: str, task: str, fake: bool, mode: str | None = None, record: bool = True):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.task = task
        self.fake = fake
        self.mode = mode or ("fake" if fake else sandbox_mode())  # fake | local | createos | replay
        self.replay_of: str | None = None
        self.recorded_mode: str | None = None
        self.created = time.time()
        self._rec = None
        if record:
            try:
                RUNS_DIR.mkdir(parents=True, exist_ok=True)
                self._rec = open(RUNS_DIR / f"{self.id}.jsonl", "a", encoding="utf-8")  # noqa: SIM115
                self._rec.write(json.dumps({"kind": "_meta", "run_id": self.id, "url": url, "task": task,
                                            "mode": self.mode, "created": self.created,
                                            "stripe": stripe_enabled()}) + "\n")
                self._rec.flush()
            except OSError as e:
                log.warning("cannot record run %s: %s", self.id, e)
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.phase = "boot"
        self.result: Any = None
        self.error: str | None = None
        self.api_url: str | None = None
        self.curl: str | None = None
        self.api_key: str | None = None
        self.api_sandbox_id: str | None = None
        self.api_status: str | None = None
        self._lock = threading.Lock()

    def append(self, ev: dict[str, Any]) -> None:
        with self._lock:
            ev["seq"] = len(self.events)
            self.events.append(ev)
            if self._rec:
                try:
                    self._rec.write(json.dumps(ev) + "\n")
                    self._rec.flush()
                except (OSError, ValueError):
                    pass
            kind, data = ev["kind"], ev["data"]
            if kind == "status" and data.get("phase"):
                self.phase = data["phase"]
            elif kind == "publish":
                self.api_url = data.get("api_url") or self.api_url
                self.curl = data.get("curl") or self.curl
                self.api_sandbox_id = data.get("sandbox_id") or self.api_sandbox_id
            elif kind == "billing" and data.get("api_key"):
                self.api_key = data["api_key"]
            elif kind == "done":
                self.api_url = data.get("api_url") or self.api_url

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e["kind"]] = counts.get(e["kind"], 0) + 1
        return {
            "run_id": self.id,
            "url": self.url,
            "task": self.task,
            "fake": self.fake,
            "mode": self.mode,
            "replay_of": self.replay_of,
            "recorded_mode": self.recorded_mode,
            "phase": self.phase,
            "done": self.done,
            "error": self.error,
            "api_url": self.api_url,
            "curl": self.curl,
            "created": self.created,
            "event_count": len(self.events),
            "counts": counts,
            "result": _jsonable(self.result),
        }


class RunSink:
    """EventSink implementation that records events on a Run."""

    def __init__(self, run: Run):
        self.run = run

    def emit(self, kind: EventKind, message: str, **data: Any) -> None:
        ev = Event(kind=kind, message=message, data=_jsonable(data)).to_dict()
        self.run.append(ev)
        if kind not in ("screenshot",):
            short = {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v) for k, v in data.items()}
            log.info("[%s] %s %s", kind, message, short if short else "")


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        from dataclasses import asdict

        return _jsonable(asdict(v))
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


RUNS: dict[str, Run] = {}


def _pick_pipeline(fake: bool):
    if not fake:
        try:
            from unclick import pipeline  # type: ignore

            if sandbox_mode() == "local":
                return _local_pipeline, False
            return pipeline.run, False
        except Exception as e:  # noqa: BLE001
            log.warning("unclick.pipeline not importable (%s); using fake pipeline", e)
    from app import fake_pipeline

    return fake_pipeline.run, True


def _local_pipeline(url: str, task: str, sink) -> Any:
    """The real pipeline + real agent, with unclick.fakes sandboxes and a local Chrome instead of CreateOS."""
    from unclick import pipeline  # type: ignore
    from unclick.fakes import local_desktop_factory

    os.environ.setdefault("UNCLICK_FORK", "0")  # a local "fork" would only be a directory copy: don't show one
    sink.emit("log", "LOCAL SANDBOX MODE: no CreateOS. Sandboxes are local processes on this machine, Chrome is the "
                     "local Chrome, and egress, fork, ingress and auto-pause are not enforced.", level="warn",
              sandbox_mode="local")
    return pipeline.run(url, task, sink, factory=local_factory(), desktop_factory=local_desktop_factory)


def _replay_path(name: str) -> Path:
    """runs/<name>.jsonl (or a path to a .jsonl file)."""
    p = Path(name)
    if p.suffix != ".jsonl":
        p = Path(str(p) + ".jsonl")
    for c in (RUNS_DIR / p.name, ROOT / p, p):
        if c.is_file():
            return c
    raise HTTPException(404, f"recording {name!r} not found in {RUNS_DIR}")


def _load_recording(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if str(ev.get("kind", "")).startswith("_"):
                meta = meta or ev
            else:
                events.append(ev)
    return meta, events


def _replay_thread(run: Run, events: list[dict[str, Any]], speed: float) -> None:
    """Re-emit recorded events with their original spacing (divided by speed); timestamps are re-based to now."""
    try:
        ts0 = float(events[0].get("ts", 0)) if events else 0.0
        t_start = time.time()
        for ev in events:
            due = t_start + (float(ev.get("ts", ts0)) - ts0) / speed
            wait = due - time.time()
            if wait > 0:
                time.sleep(min(wait, 30))
            out = {k: v for k, v in ev.items() if k != "seq"}
            out["ts"] = time.time()
            run.append(out)
    finally:
        run.done = True


def _run_thread(run: Run, fn) -> None:
    sink = RunSink(run)
    try:
        run.result = fn(run.url, run.task, sink)
        if not any(e["kind"] == "done" for e in run.events):
            api = getattr(run.result, "api", None)
            ok = getattr(run.result, "ok", True)
            sink.emit(
                "done",
                "Run finished" if ok else "Run finished with errors",
                summary="ok" if ok else (getattr(run.result, "error", None) or "failed"),
                api_url=getattr(api, "api_url", None),
            )
    except Exception as e:  # noqa: BLE001
        run.error = f"{type(e).__name__}: {e}"
        sink.emit("error", run.error, where="pipeline", traceback=traceback.format_exc()[-4000:])
        sink.emit("status", "Run failed", phase="failed")
        sink.emit("done", "Run failed", summary=run.error)
    finally:
        run.done = True


class RunIn(BaseModel):
    url: str = ""
    task: str = ""
    replay: str | None = None   # name of a recording in runs/ (or set UNCLICK_REPLAY)
    speed: float | None = None  # replay speed factor (UNCLICK_REPLAY_SPEED, default 1)


@app.post("/api/runs")
def start_run(body: RunIn) -> dict[str, Any]:
    replay = body.replay or os.getenv("UNCLICK_REPLAY", "").strip() or None
    if replay:
        path = _replay_path(replay)
        meta, events = _load_recording(path)
        if not events:
            raise HTTPException(400, f"recording {path.name} has no events")
        speed = max(0.1, float(body.speed or os.getenv("UNCLICK_REPLAY_SPEED", "1") or 1))
        rec_mode = meta.get("mode") or "unknown"
        run = Run(meta.get("url") or body.url.strip(), meta.get("task") or body.task.strip(),
                  fake=rec_mode == "fake", mode="replay", record=False)
        run.replay_of = path.name
        run.recorded_mode = rec_mode
        RUNS[run.id] = run
        threading.Thread(target=_replay_thread, args=(run, events, speed), daemon=True,
                         name=f"replay-{run.id}").start()
        return {"run_id": run.id, "fake": run.fake, "mode": "replay", "replay_of": path.name,
                "recorded_mode": rec_mode, "speed": speed, "url": run.url, "task": run.task}
    if not body.url.strip() or not body.task.strip():
        raise HTTPException(400, "url and task are required")
    fn, is_fake = _pick_pipeline(fake_mode())
    run = Run(body.url.strip(), body.task.strip(), is_fake)
    RUNS[run.id] = run
    threading.Thread(target=_run_thread, args=(run, fn), daemon=True, name=f"run-{run.id}").start()
    return {"run_id": run.id, "fake": is_fake, "mode": run.mode}


@app.get("/api/replays")
def list_replays() -> list[dict[str, Any]]:
    """Recordings in runs/ (newest first) with their meta line."""
    out = []
    if RUNS_DIR.is_dir():
        for p in sorted(RUNS_DIR.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime):
            meta: dict[str, Any] = {}
            try:
                with open(p, encoding="utf-8") as f:
                    first = json.loads(f.readline() or "{}")
                    meta = first if str(first.get("kind", "")).startswith("_") else {}
            except (OSError, ValueError):
                pass
            out.append({"name": p.stem, "size": p.stat().st_size, "mode": meta.get("mode"), "url": meta.get("url"),
                        "task": meta.get("task"), "created": meta.get("created")})
    return out


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return [r.summary() for r in sorted(RUNS.values(), key=lambda r: -r.created)]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _get(run_id).summary()


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    run = _get(run_id)

    async def gen():
        i = 0
        last_ping = time.time()
        while True:
            if await request.is_disconnected():
                break
            while i < len(run.events):
                ev = run.events[i]
                i += 1
                yield {"event": "message", "id": str(ev["seq"]), "data": json.dumps(ev)}
            # Stay open after "done": Try it, Wake test and Checkout keep adding events.
            if time.time() - last_ping > 15:
                last_ping = time.time()
                yield {"event": "ping", "data": "{}"}
            await asyncio.sleep(0.1)

    return EventSourceResponse(gen())


def _get(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


# ---------------------------------------------------------------------------
# Try it + wake test (the API card on the final screen)
# ---------------------------------------------------------------------------


class TryIn(BaseModel):
    run_id: str | None = None
    api_url: str | None = None
    key: str | None = None  # a key bought through Checkout; defaults to the demo key in the curl


def _headers_from_curl(curl: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in re.findall(r"-H\s+['\"]([^:'\"]+):\s*([^'\"]*)['\"]", curl or ""):
        headers[name.strip()] = value.strip()
    return headers


@app.post("/api/try")
async def try_api(body: TryIn) -> JSONResponse:
    """Call the published API server-side (avoids CORS) and return its JSON."""
    run = RUNS.get(body.run_id or "")
    api_url = body.api_url or (run.api_url if run else None)
    if not api_url:
        raise HTTPException(400, "no api_url")
    sink = RunSink(run) if run else None
    if run and run.mode == "replay" and run.recorded_mode != "fake":
        return JSONResponse({"status": 409, "ms": 0, "json": {
            "error": f"This is a replay of {run.replay_of}; the API it published is not running now. "
                     "Start a new run to get a live API."}}, status_code=409)
    if run and run.fake:
        from app import fake_pipeline

        t0 = time.time()
        await asyncio.sleep(0.25)
        ms = (time.time() - t0) * 1000
        data = fake_pipeline.SAMPLE_OUTPUT
        if sink:
            fake_pipeline.charge_call(sink)
        return JSONResponse({"status": 200, "ms": round(ms, 1), "json": data})

    import httpx

    headers = _headers_from_curl(run.curl if run else None)
    key = body.key or (run.api_key if run else None)
    if key:
        headers = {k: v for k, v in headers.items() if k.lower() != "x-unclick-key"}
        headers["X-Unclick-Key"] = key
    demo_headers = _headers_from_curl(run.curl if run else None)
    t0 = time.time()
    key_rejected = False
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(api_url, headers=headers)
            if r.status_code == 401 and key and headers != demo_headers and demo_headers:
                # A key stored in the browser (bought for another API, or a stale test key) was rejected:
                # fall back to this API's own demo key from the publish curl.
                key_rejected = True
                t0 = time.time()
                r = await client.get(api_url, headers=demo_headers)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": 0, "ms": (time.time() - t0) * 1000, "error": str(e)}, status_code=502)
    ms = (time.time() - t0) * 1000
    try:
        payload: Any = r.json()
    except Exception:  # noqa: BLE001
        payload = r.text[:5000]
    remaining = r.headers.get("X-Unclick-Calls-Remaining")
    if sink and r.status_code == 200 and remaining is not None:
        if stripe_enabled():
            sink.emit("billing", "API call charged (Stripe meter event)", event="call_charged",
                      calls_remaining=int(remaining), amount_cents=_cents_per_call(), metered=True)
        else:
            sink.emit("billing", "Demo key call counted (Stripe not configured: not metered)", event="call_charged",
                      calls_remaining=int(remaining), metered=False)
    elif sink and r.status_code == 402:
        sink.emit("billing", "Out of calls: 402 Payment Required", event="call_charged", calls_remaining=0)
    return JSONResponse({"status": r.status_code, "ms": round(ms, 1), "json": payload, "key_rejected": key_rejected})


def _cents_per_call() -> float | None:
    try:
        from unclick import billing  # type: ignore

        return round(billing.pack_cents() / max(1, billing.pack_calls()), 2)
    except Exception:  # noqa: BLE001
        return None


class WakeIn(BaseModel):
    api_url: str | None = None
    run_id: str | None = None


@app.post("/api/wake")
async def wake(body: WakeIn) -> dict[str, Any]:
    run = RUNS.get(body.run_id or "")
    api_url = body.api_url or (run.api_url if run else None)
    sink = RunSink(run) if run else None
    if not api_url:
        raise HTTPException(400, "no api_url")

    if run and run.mode == "replay" and run.recorded_mode != "fake":
        raise HTTPException(409, f"replay of {run.replay_of}: its API is not running now")
    if (run and run.fake) or (run is None and fake_mode()):
        from app import fake_pipeline

        return await asyncio.to_thread(fake_pipeline.fake_wake, sink)

    try:
        from unclick.publish import wake_and_time  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(501, f"unclick.publish.wake_and_time unavailable: {e}") from e

    if run and run.mode == "local":
        # A local process never pauses: report a plain /health round trip, not a fake wake-up.
        ms = await asyncio.to_thread(wake_and_time, api_url)
        if sink:
            sink.emit("log", f"Local API answered /health in {ms:.0f} ms (local sandbox mode: no auto-pause, "
                             "so there is nothing to wake)", level="info")
        return {"wake_ms": ms, "local": True}

    sandbox = _api_sandbox(run)
    try:
        wake_ms = await asyncio.to_thread(wake_and_time, api_url, sink, sandbox)
    except Exception as e:  # noqa: BLE001
        if sink:
            sink.emit("error", f"Wake test failed: {e}", where="wake")
        raise HTTPException(502, str(e)) from e
    if sink and sandbox is not None and run.mode != "local":
        sink.emit("sandbox", "API sandbox resumed on request", id=sandbox.id, role="api", action="resumed", ms=wake_ms)
        run.api_status = "running"
    return {"wake_ms": wake_ms}


def _api_sandbox(run: Run | None):
    if not run or not run.api_sandbox_id or run.fake or run.mode == "replay":
        return None
    if run.mode == "local":
        return local_factory().get(run.api_sandbox_id)
    try:
        from unclick.sandbox import default_factory  # type: ignore

        return default_factory().get(run.api_sandbox_id)
    except Exception as e:  # noqa: BLE001
        log.warning("could not get API sandbox %s: %s", run.api_sandbox_id, e)
        return None


@app.get("/api/runs/{run_id}/api_status")
async def api_status(run_id: str) -> dict[str, Any]:
    """Polled by the UI after publish: surfaces the auto-pause as it happens."""
    run = _get(run_id)
    sb = await asyncio.to_thread(_api_sandbox, run)
    if sb is None:
        return {"status": run.api_status}
    try:
        st = await asyncio.to_thread(sb.status)
    except Exception as e:  # noqa: BLE001
        return {"status": run.api_status, "error": str(e)}
    if st != run.api_status:
        sink = RunSink(run)
        if st == "paused":
            sink.emit("sandbox", "API sandbox auto-paused (idle)", id=sb.id, role="api", action="paused")
            sink.emit("pause", "API is paused: vCPU billing stopped", state="paused")
        elif st == "pausing":
            sink.emit("sandbox", "API sandbox idle: auto-pausing", id=sb.id, role="api", action="pausing")
        run.api_status = st
    return {"status": st}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "fake": fake_mode(),
        "sandbox": sandbox_mode(),
        "replay": os.getenv("UNCLICK_REPLAY", "").strip() or None,
        "pipeline": _pipeline_available(),
        "billing": BILLING_MOUNTED,
        "stripe": stripe_enabled(),
        "stripe_pk": os.getenv("STRIPE_PUBLISHABLE_KEY", "") if (os.getenv("STRIPE_PUBLISHABLE_KEY", "").startswith("pk_")) else "",
    }


def _pipeline_available() -> bool:
    try:
        from unclick import pipeline  # type: ignore  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Billing (publish-billing session) — mounted if importable
# ---------------------------------------------------------------------------

BILLING_MOUNTED = False
try:
    from unclick.billing import router as billing_router  # type: ignore

    from unclick import billing as _billing  # type: ignore

    app.include_router(billing_router)
    BILLING_MOUNTED = True

    def _billing_to_run(kind: EventKind, message: str, **data: Any) -> None:
        """Checkout/payment events have no run id: show them on the newest published (else newest) run."""
        runs = sorted(RUNS.values(), key=lambda r: -r.created)
        target = next((r for r in runs if r.api_url), runs[0] if runs else None)
        if target:
            RunSink(target).emit(kind, message, **data)

    _billing.add_listener(_billing_to_run)
except Exception as _e:  # noqa: BLE001
    log.warning("unclick.billing.router not mounted (%s); Buy uses the fake checkout", _e)


@app.post("/api/fake_checkout")
async def fake_checkout(request: Request) -> dict[str, Any]:
    """Fake-pipeline runs (or no billing module): pretend a test-mode purchase succeeded."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    run = RUNS.get((body or {}).get("run_id") or "")
    if run:
        from app import fake_pipeline

        await asyncio.to_thread(fake_pipeline.fake_purchase, RunSink(run))
    return {"checkout_url": None, "fake": True}


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
