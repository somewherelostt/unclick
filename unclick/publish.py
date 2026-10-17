"""Publish a verified client.py as a paid, auto-pausing HTTPS API on a CreateOS sandbox.

publish(source_sandbox, client_code, sink, name) -> PublishedApi
  1. Pause the verified source sandbox, then FORK it with a tighter policy:
     egress = [backend host(s) the client calls, api.stripe.com], ingress on.
     Same warm state (packages the client needed are already there), different network policy.
  2. Upload client.py + api_template/server.py (stdlib only, no pip) + config.json into
     /srv/unclick, start it with start_process, wait until /health answers on the public URL.
  3. set_auto_pause(60): idle API sandboxes pause and wake on the next request.
  4. Destroy the source to free the plan slot.
  Fallback (fork refused, e.g. Free plan = 1 concurrent sandbox): wait until the source is
  running again (resume it, retrying), apply the same egress + ingress, and publish there
  (warning event). Every sandbox write retries once after a 409 (sandbox not running).
  UNCLICK_FORK=0 skips the fork attempt entirely.

wake_and_time(api_url, sink=None, sandbox=None) -> float
  Hit /health on a (paused) API and return the wake latency in ms.

wait_until_paused(sandbox, sink=None, timeout=180) -> float
  Poll until the auto-pause kicks in; returns seconds waited.

Billing: see unclick/billing.py. The sandbox gets the Stripe test key in config.json (never in
events or logs) and validates keys against Stripe customer metadata itself.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from unclick import billing, schema
from unclick.contracts import EventSink, PublishedApi, Sandbox

TEMPLATE_DIR = Path(__file__).parent / "api_template"
REMOTE_DIR = "/srv/unclick"
PORT = 8000
STRIPE_HOST = "api.stripe.com"
_URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_LOCAL = {"localhost", "127.0.0.1", "0.0.0.0"}
_POLL = 1.5  # seconds between status polls while waiting for a sandbox to be running


def hosts_from_code(code: str) -> list[str]:
    """Backend hosts the client calls, read from URL literals in the code."""
    seen: list[str] = []
    for h in _URL_HOST.findall(code or ""):
        h = h.lower().rstrip(".")
        if h not in _LOCAL and h not in seen:
            seen.append(h)
    return seen


def tool_name(name: str) -> str:
    """MCP tool name the API server derives from its config name (keep in sync with server.tool_name)."""
    slug = re.sub(r"_+", "_", "".join(c if c.isalnum() else "_" for c in name.lower())).strip("_")
    return ("get_" + (slug or "data"))[:64]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _base(api_url: str) -> str:
    u = urlsplit(api_url)
    return f"{u.scheme}://{u.netloc}"


def _wait_public_health(base: str, timeout: float) -> tuple[bool, str]:
    """Poll {base}/health until it returns our JSON. Tries http:// too if TLS isn't ready."""
    deadline = time.monotonic() + timeout
    urls = [base + "/health"]
    if base.startswith("https://"):
        urls.append("http://" + base[len("https://"):] + "/health")
    last = ""
    with httpx.Client(timeout=15, follow_redirects=True) as c:
        while time.monotonic() < deadline:
            for u in urls:
                try:
                    r = c.get(u)
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                        body = r.json()
                        if "ok" in body:
                            return bool(body["ok"]), u
                    last = f"{r.status_code} {r.text[:120]!r}"
                except (httpx.HTTPError, ValueError) as e:
                    last = f"{type(e).__name__}: {e}"
            time.sleep(1.0)
    return False, last


def _remote_log(sb: Sandbox, lines: int = 40) -> str:
    try:
        return sb.exec(f"tail -n {lines} /tmp/unclick-api.log 2>&1", timeout=15).stdout
    except Exception as e:  # noqa: BLE001
        return f"(could not read log: {e})"


def _is_409(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 409 or "409" in str(e)[:200]


def _ensure_running(sb: Sandbox, sink: EventSink, timeout: float = 150.0) -> None:
    """Block until sb is running, resuming it if paused. Writes to a paused sandbox return 409.

    After a refused fork the source can still be pausing/paused (or briefly 'forking'), and a
    resume can itself fail transiently, so poll and retry instead of a single status check.
    """
    deadline = time.monotonic() + timeout
    t0 = time.perf_counter()
    resumed = False
    last = ""
    while time.monotonic() < deadline:
        try:
            last = sb.status()
        except Exception as e:  # noqa: BLE001
            last = f"unknown ({type(e).__name__})"
        if last == "running":
            if resumed:
                sink.emit("sandbox", "Source sandbox resumed", id=sb.id, role="api", action="resumed", ms=_ms(t0))
            return
        if last in ("paused", "error"):
            try:
                sb.resume()
                resumed = True
                continue
            except Exception as e:  # noqa: BLE001
                sink.emit("log", f"Resume attempt failed ({type(e).__name__}: {str(e)[:120]}); retrying",
                          level="warn")
        time.sleep(_POLL)
    raise RuntimeError(f"sandbox {sb.id} did not reach 'running' (last status {last!r}); cannot publish")


def _call(sb: Sandbox, sink: EventSink, fn, *args: Any, **kwargs: Any) -> Any:
    """Run a sandbox operation; on 409 (not running) wait until running and retry once."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        if not _is_409(e):
            raise
        sink.emit("log", f"Sandbox not running ({str(e)[:100]}); resuming and retrying", level="warn")
        _ensure_running(sb, sink)
        return fn(*args, **kwargs)


def _fork_or_fallback(src: Sandbox, allow: list[str], sink: EventSink,
                      try_fork: bool = True) -> tuple[Sandbox, bool]:
    """Returns (api_sandbox, forked)."""
    if not try_fork:
        sink.emit("log", "Fork disabled (UNCLICK_FORK=0): publishing in the source sandbox", level="info")
        return _fallback(src, allow, sink), False
    try:
        sink.emit("sandbox", "Pausing the verified sandbox to snapshot it", id=src.id, role="runner",
                  action="pausing")
        t0 = time.perf_counter()
        src.pause()
        sink.emit("sandbox", "Verified sandbox paused (RAM + disk snapshot)", id=src.id, role="runner",
                  action="paused", ms=_ms(t0))
        t0 = time.perf_counter()
        child = src.fork(egress=allow, ingress=True)
        sink.emit("sandbox", f"Forked API sandbox from {src.id}: same warm state, tighter policy",
                  id=child.id, role="api", action="forked", forked_from=src.id, ms=_ms(t0))
        return child, True
    except Exception as e:  # noqa: BLE001  (429 plan cap, 409, timeout...)
        sink.emit("log", f"Fork unavailable ({type(e).__name__}: {str(e)[:160]}); publishing in the "
                         f"source sandbox instead", level="warn")
    return _fallback(src, allow, sink), False


def _fallback(src: Sandbox, allow: list[str], sink: EventSink) -> Sandbox:
    """Publish in the source itself: make sure it is running (resume if paused), then apply policy."""
    _ensure_running(src, sink)
    _call(src, sink, src.set_egress, allow)
    _call(src, sink, src.enable_ingress)
    return src


def publish(source_sandbox: Sandbox, client_code: str, sink: EventSink, name: str, *,
            egress_hosts: list[str] | None = None, source_site: str = "", demo_calls: int = 100,
            auto_pause_seconds: int = 60, destroy_source: bool = True,
            health_timeout: float = 120.0, try_fork: bool | None = None, task: str = "",
            sample_output: Any = None) -> PublishedApi:
    """Publish client_code (must define run(**params)) as a paid API. See module docstring.

    try_fork=None reads env UNCLICK_FORK (default on). Set UNCLICK_FORK=0 on the Free plan to skip
    the pause + refused fork + resume round trip (~7 s).
    """
    src = source_sandbox
    if try_fork is None:
        try_fork = os.environ.get("UNCLICK_FORK", "1").strip().lower() not in ("0", "false", "no", "off")
    sink.emit("status", f"Publishing {name} as an API", phase="publish")

    hosts = list(egress_hosts or hosts_from_code(client_code))
    if not hosts:
        try:
            hosts = [h for h in src.get_egress() if h not in ("*",)]
        except Exception:  # noqa: BLE001
            hosts = []
    allow = list(dict.fromkeys([*hosts, STRIPE_HOST]))

    api_sb, forked = _fork_or_fallback(src, allow, sink, try_fork)
    sink.emit("egress", "Published API egress: only the backend and Stripe", allow=allow,
              added=[STRIPE_HOST], reason="published API: backend host(s) + Stripe metering")

    try:
        demo_key = billing.issue_demo_key(demo_calls)
    except Exception as e:  # noqa: BLE001  Stripe down: demo still works, unmetered
        sink.emit("log", f"Stripe demo key failed ({type(e).__name__}); using a local demo key", level="warn")
        demo_key = f"uk_demo_{__import__('secrets').token_hex(16)}"
    cfg: dict[str, Any] = {
        "name": name, "source_site": source_site, "task": task, "api_id": api_sb.id, "port": PORT,
        "demo_key": demo_key, "demo_calls": demo_calls, "buy_url": billing.buy_url(api_sb.id),
        **billing.sandbox_config(),
    }
    if not cfg.get("stripe_key"):
        sink.emit("log", "Stripe billing is off (no sk_test_ key): API runs unmetered with the demo key",
                  level="warn")

    server_code = (TEMPLATE_DIR / "server.py").read_text(encoding="utf-8")
    _ensure_running(api_sb, sink)
    _call(api_sb, sink, api_sb.write_file, f"{REMOTE_DIR}/client.py", client_code)
    _call(api_sb, sink, api_sb.write_file, f"{REMOTE_DIR}/server.py", server_code)
    _call(api_sb, sink, api_sb.write_file, f"{REMOTE_DIR}/config.json", json.dumps(cfg))
    data_schema = schema.infer_schema(sample_output) if sample_output is not None else {}
    _call(api_sb, sink, api_sb.write_file, f"{REMOTE_DIR}/schema.json", json.dumps(data_schema, indent=1))
    if data_schema:
        fields = schema.describe(data_schema)
        sink.emit("log", "Typed schema inferred from the verified output: "
                  + ", ".join(f"{f['name']}: {f['type']}" for f in fields)[:400], level="info")
    _call(api_sb, sink, api_sb.exec,
          f"chmod 600 {REMOTE_DIR}/config.json 2>/dev/null; pkill -f '[s]rv/unclick/server.py' 2>/dev/null; true",
          timeout=20)
    _call(api_sb, sink, api_sb.start_process,
          f"cd {REMOTE_DIR} && exec python3 -u {REMOTE_DIR}/server.py >> /tmp/unclick-api.log 2>&1")

    local = _call(api_sb, sink, api_sb.exec, 
        f"for i in $(seq 1 60); do curl -s --max-time 2 http://127.0.0.1:{PORT}/health && exit 0; sleep 0.5; done; exit 1",
        timeout=45)
    if local.exit_code != 0:
        log = _remote_log(api_sb)
        sink.emit("error", f"API server did not start: {log[-400:]}", where="publish")
        raise RuntimeError(f"API server did not start in sandbox {api_sb.id}:\n{log}")
    try:
        if not json.loads(local.stdout or "{}").get("ok", False):
            sink.emit("log", f"Server is up but client.py failed to import: {local.stdout[-300:]}", level="warn")
    except ValueError:
        pass

    base = api_sb.preview_url(PORT)
    ok, detail = _wait_public_health(base, health_timeout)
    if ok and detail.startswith("http://") and base.startswith("https://"):
        base = "http://" + base[len("https://"):]  # TLS not provisioned for this host yet
    if not ok:
        sink.emit("log", f"Public URL not answering yet ({detail}); it may need a moment", level="warn")

    _call(api_sb, sink, api_sb.set_auto_pause, auto_pause_seconds)
    sink.emit("log", f"Auto-pause after {auto_pause_seconds}s idle; the next request wakes it", level="info")

    api_url = base + "/v1/run"
    mcp_url = base + "/mcp"
    mcp_config = json.dumps({"mcpServers": {name: {"url": mcp_url, "headers": {"X-Unclick-Key": demo_key}}}},
                            indent=2)
    api = PublishedApi(
        api_url=api_url,
        docs_url=base + "/docs",
        curl=f'curl -H "X-Unclick-Key: {demo_key}" "{api_url}"',
        sandbox_id=api_sb.id,
        openapi_url=base + "/openapi.json",
        mcp_url=mcp_url,
        mcp_config=mcp_config,
    )
    sink.emit("publish", f"Live at {api_url}", api_url=api.api_url, docs_url=api.docs_url, curl=api.curl,
              sandbox_id=api.sandbox_id, api_id=api.sandbox_id, openapi_url=api.openapi_url, mcp_url=api.mcp_url,
              mcp_config=api.mcp_config, tool_name=tool_name(name), name=name, source_url=source_site, task=task,
              schema=data_schema, fields=schema.describe(data_schema) if data_schema else [])

    if forked and destroy_source:
        try:
            src.destroy()
            sink.emit("sandbox", "Source sandbox destroyed (plan slot freed)", id=src.id, role="runner",
                      action="destroyed")
        except Exception as e:  # noqa: BLE001
            sink.emit("log", f"Could not destroy source sandbox {src.id}: {e}", level="warn")
    return api


def wait_until_paused(sandbox: Sandbox, sink: EventSink | None = None, timeout: float = 180.0) -> float:
    """Block until the sandbox reports paused (auto-pause). Returns seconds waited."""
    t0 = time.monotonic()
    last = ""
    while time.monotonic() - t0 < timeout:
        last = sandbox.status()
        if last == "paused":
            waited = round(time.monotonic() - t0, 1)
            if sink:
                sink.emit("sandbox", "API sandbox auto-paused", id=sandbox.id, role="api", action="paused")
                sink.emit("pause", "API is paused: vCPU billing stopped", state="paused")
            return waited
        time.sleep(3)
    raise TimeoutError(f"sandbox {sandbox.id} not paused after {timeout}s (status {last!r})")


def wake_and_time(api_url: str, sink: EventSink | None = None, sandbox: Sandbox | None = None,
                  timeout: float = 120.0) -> float:
    """Call a paused API's /health (free, uncharged) and return wake latency in ms.

    Wake-on-request: the ingress resumes the sandbox; until our server answers, the proxy may
    hold the request or serve a waiting page, so we poll until our JSON comes back.
    """
    if sandbox is not None and sink is not None:
        try:
            if sandbox.status() == "paused":
                sink.emit("pause", "API is paused", state="paused")
        except Exception:  # noqa: BLE001
            pass
    if sink:
        sink.emit("pause", "Calling the paused API: wake-on-request", state="waking")
    url = _base(api_url) + "/health"
    t0 = time.perf_counter()
    deadline = time.monotonic() + timeout
    last = ""
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        while time.monotonic() < deadline:
            try:
                r = c.get(url)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json") \
                        and "ok" in r.json():
                    ms = _ms(t0)
                    if sink:
                        sink.emit("pause", f"Awake in {ms:.0f} ms", state="awake", wake_ms=ms)
                    return ms
                last = f"{r.status_code}"
            except (httpx.HTTPError, ValueError) as e:
                last = f"{type(e).__name__}"
            time.sleep(0.25)
    raise TimeoutError(f"API at {url} did not wake within {timeout}s (last: {last})")
