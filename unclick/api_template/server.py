"""Unclick published API server. Runs INSIDE the API sandbox next to client.py.

Zero dependencies (stdlib only), so nothing needs pip and egress can stay locked to
[backend host, api.stripe.com]. It also boots instantly after a wake-on-request.

Routes (bind 0.0.0.0:8000):
  GET|POST /v1/run   query params + JSON body -> client.run(**params) -> JSON. Needs X-Unclick-Key.
  GET      /health   {"ok": true, ...}. No key, never charged (used for wake timing).
  GET      /         small HTML page: name, source site, example curl.
  GET      /docs     same page (PublishedApi.docs_url points here).

Billing (prepaid credits, Stripe test mode):
  * API keys look like  uk_<stripe customer id>_<32 hex>  e.g. uk_cus_Q1w2e3_ab12...
    The Stripe Customer (created by unclick/billing.py at Checkout success) holds metadata:
      unclick_key_sha256       sha256 of the full key
      unclick_calls_purchased  cumulative prepaid calls
      unclick_calls_used       calls used so far (written back by this server)
    So keys bought AFTER publishing work immediately: nothing is pushed to the sandbox.
  * A key is validated once by GET /v1/customers/{id} (then cached in RAM; RAM survives
    pause/resume). A local counter decides instantly; out of credits -> 402 (after one
    refetch, in case the buyer topped up).
  * Each successful call records a Billing Meter event (event_name unclick_api_call,
    payload stripe_customer_id + value=1) and updates unclick_calls_used, both on a
    background thread so the response is not slowed down. Failed calls are not charged.
  * The demo key (config demo_key, demo_calls) is checked locally without Stripe, so the
    live demo never blocks; its calls are still metered to the demo customer when known.

Config: /srv/unclick/config.json (written by unclick/publish.py, chmod 600), or the path
in UNCLICK_CONFIG. Keys: name, source_site, api_id, stripe_key, demo_key, demo_calls,
meter_event, buy_url, port.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import os
import queue
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("UNCLICK_CONFIG", os.path.join(HERE, "config.json"))
STRIPE_API = "https://api.stripe.com/v1"
KEY_HEADER = "X-Unclick-Key"
CACHE_TTL = 300  # seconds before a paid key's purchased count is refetched anyway


def load_config() -> dict:
    cfg: dict = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("name", "Unclick API")
    cfg.setdefault("source_site", "")
    cfg.setdefault("api_id", "")
    cfg.setdefault("stripe_key", os.environ.get("STRIPE_SECRET_KEY", ""))
    cfg.setdefault("demo_key", "")
    cfg.setdefault("demo_calls", 100)
    cfg.setdefault("meter_event", "unclick_api_call")
    cfg.setdefault("buy_url", "")
    cfg.setdefault("port", 8000)
    key = cfg["stripe_key"]
    if key and not (key.startswith("sk_test_") or key.startswith("rk_test_")):
        print("refusing non-test Stripe key; billing disabled", file=sys.stderr, flush=True)
        cfg["stripe_key"] = ""
    return cfg


CFG = load_config()


# ---------------------------------------------------------------------------
# Stripe (raw HTTPS, form-encoded; no SDK in the sandbox)
# ---------------------------------------------------------------------------


def _flatten(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        name = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out.extend(_flatten(v, name))
        else:
            out.append((name, str(v)))
    return out


def stripe_call(method: str, path: str, params: dict | None = None,
                idempotency_key: str | None = None) -> dict:
    data = urllib.parse.urlencode(_flatten(params or {})).encode() if params else None
    req = urllib.request.Request(STRIPE_API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + CFG["stripe_key"])
    req.add_header("Stripe-Version", "2025-03-31.basil")
    if idempotency_key:
        req.add_header("Idempotency-Key", idempotency_key)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"stripe {method} {path} -> {e.code}: {body}") from None


def parse_key(key: str) -> str | None:
    """uk_cus_XXXX_<hex> -> cus_XXXX (None if malformed)."""
    if not key.startswith("uk_cus_"):
        return None
    head, _, secret = key.rpartition("_")
    if len(secret) < 16 or not head.startswith("uk_cus_"):
        return None
    return head[3:]


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------


class Ledger:
    """Local credit counters. Authoritative for instant 402s; Stripe is the record."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.accounts: dict[str, dict] = {}  # key sha -> {customer, purchased, used, fetched}
        demo = CFG.get("demo_key") or ""
        if demo:
            self.accounts[sha256(demo)] = {
                "customer": parse_key(demo), "purchased": int(CFG["demo_calls"]),
                "used": 0, "fetched": float("inf"), "demo": True,
            }

    def _fetch(self, key: str, key_sha: str, keep_used: int | None) -> dict | None:
        cus = parse_key(key)
        if not cus or not CFG["stripe_key"]:
            return None
        try:
            c = stripe_call("GET", f"/customers/{cus}")
        except Exception as e:  # unknown customer, Stripe down, egress blocked
            print(f"key lookup failed: {e}", file=sys.stderr, flush=True)
            return None
        md = c.get("metadata") or {}
        if c.get("deleted") or md.get("unclick_key_sha256") != key_sha:
            return None
        used = int(md.get("unclick_calls_used") or 0)
        return {
            "customer": cus,
            "purchased": int(md.get("unclick_calls_purchased") or 0),
            "used": max(used, keep_used or 0),
            "fetched": time.time(),
            "demo": False,
        }

    def charge(self, key: str) -> tuple[bool, dict | None]:
        """Reserve one call. Returns (allowed, account). account None = invalid key."""
        key_sha = sha256(key)
        with self.lock:
            acct = self.accounts.get(key_sha)
        if acct is None or (not acct["demo"] and time.time() - acct["fetched"] > CACHE_TTL):
            fresh = self._fetch(key, key_sha, acct["used"] if acct else None)
            if fresh is not None:
                with self.lock:
                    self.accounts[key_sha] = acct = fresh
            elif acct is None:
                return False, None  # unknown key -> 401
            # else: stale cache and Stripe unreachable -> keep serving from the cache
        with self.lock:
            if acct["used"] < acct["purchased"]:
                acct["used"] += 1
                return True, acct
        if not acct["demo"]:  # maybe they topped up since we cached
            fresh = self._fetch(key, key_sha, acct["used"])
            if fresh:
                with self.lock:
                    self.accounts[key_sha] = acct = fresh
                    if acct["used"] < acct["purchased"]:
                        acct["used"] += 1
                        return True, acct
        return False, acct

    def refund(self, acct: dict) -> None:
        with self.lock:
            acct["used"] = max(0, acct["used"] - 1)


LEDGER = Ledger()
METER_Q: "queue.Queue[tuple[str, int]]" = queue.Queue()
STATS = {"calls": 0, "charged": 0, "meter_ok": 0, "meter_err": 0, "started": time.time()}


def meter_worker() -> None:
    """Send meter events and write back calls_used, off the request path."""
    while True:
        cus, used = METER_Q.get()
        latest: dict[str, int] = {cus: used}
        events = [cus]
        while True:  # drain a burst so metadata is written once per customer
            try:
                c2, u2 = METER_Q.get_nowait()
            except queue.Empty:
                break
            events.append(c2)
            latest[c2] = max(latest.get(c2, 0), u2)
        for c in events:
            ident = "uc_" + uuid.uuid4().hex
            try:
                stripe_call("POST", "/billing/meter_events", {
                    "event_name": CFG["meter_event"],
                    "identifier": ident,
                    "timestamp": int(time.time()),
                    "payload": {"stripe_customer_id": c, "value": 1},
                }, idempotency_key=ident)
                STATS["meter_ok"] += 1
            except Exception as e:
                STATS["meter_err"] += 1
                print(f"meter event failed: {e}", file=sys.stderr, flush=True)
        for c, u in latest.items():
            try:
                stripe_call("POST", f"/customers/{c}", {"metadata": {"unclick_calls_used": u}})
            except Exception as e:
                print(f"usage write-back failed: {e}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

sys.path.insert(0, HERE)
CLIENT = None
CLIENT_ERROR = ""
try:
    CLIENT = importlib.import_module("client")
    if not callable(getattr(CLIENT, "run", None)):
        CLIENT_ERROR = "client.py has no run() function"
except Exception:
    CLIENT_ERROR = traceback.format_exc(limit=3)


def coerce(v: str):
    """Query strings are text; turn obvious numbers/bools into real types."""
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#111;background:#fafafa}}
code,pre{{background:#111;color:#e6e6e6;border-radius:8px}}pre{{padding:14px;overflow-x:auto}}code{{padding:2px 6px}}
.tag{{display:inline-block;background:#635bff;color:#fff;border-radius:99px;padding:2px 10px;font-size:12px}}</style></head>
<body><span class="tag">Unclick</span><h1>{name}</h1>
<p>An API generated from <a href="{site}">{site_text}</a> by an agent running in a CreateOS sandbox.
Paid per call (Stripe test mode). This sandbox auto-pauses when idle and wakes on the next request.</p>
<h3>Call it</h3><pre>{curl}</pre>
<p>Send parameters as query string or JSON body. Header <code>X-Unclick-Key</code> is required.
Out of credits returns <code>402</code>{buy}.</p>
<h3>Endpoints</h3><ul><li><code>GET|POST /v1/run</code> run the client and return JSON</li>
<li><code>GET /health</code> liveness (free)</li></ul></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Unclick/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep keys out of logs: path only, no headers
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Unclick-Key")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj, headers: dict | None = None) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json", headers)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/health":
            return self._json(200, {
                "ok": CLIENT is not None and not CLIENT_ERROR, "name": CFG["name"],
                "client_error": CLIENT_ERROR[-500:], "billing": bool(CFG["stripe_key"]),
                "uptime_s": round(time.time() - STATS["started"], 1),
                "calls": STATS["calls"], "charged": STATS["charged"],
            })
        if path in ("/", "/docs"):
            return self._send(200, self._page().encode(), "text/html; charset=utf-8")
        if path == "/v1/run":
            return self._run(url.query)
        return self._json(404, {"error": "not_found", "routes": ["/v1/run", "/health", "/"]})

    def _page(self) -> str:
        host = self.headers.get("Host", "localhost:8000")
        scheme = "http" if host.startswith(("localhost", "127.")) else "https"
        demo = CFG.get("demo_key") or "<your key>"
        curl = f'curl -H "{KEY_HEADER}: {demo}" "{scheme}://{host}/v1/run"'
        site = CFG["source_site"] or "#"
        buy = f' (<a href="{html.escape(CFG["buy_url"])}">buy calls</a>)' if CFG["buy_url"] else ""
        return PAGE.format(name=html.escape(CFG["name"]), site=html.escape(site),
                           site_text=html.escape(CFG["source_site"] or "a website"),
                           curl=html.escape(curl), buy=buy)

    def _params(self, query: str) -> dict:
        params = {k: coerce(v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)}
        n = int(self.headers.get("Content-Length") or 0)
        if self.command == "POST" and n:
            raw = self.rfile.read(n)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = dict(urllib.parse.parse_qsl(raw.decode(errors="replace")))
            if isinstance(body, dict):
                params.update(body)
        return params

    def _run(self, query: str):
        STATS["calls"] += 1
        try:
            params = self._params(query)
        except Exception as e:
            return self._json(400, {"error": "bad_request", "message": str(e)})
        key = (self.headers.get(KEY_HEADER) or "").strip()
        if not key:
            return self._json(401, {"error": "missing_key", "message": f"send header {KEY_HEADER}"})
        if CLIENT is None or CLIENT_ERROR:
            return self._json(503, {"error": "client_unavailable", "message": CLIENT_ERROR[-500:]})
        allowed, acct = LEDGER.charge(key)
        if acct is None:
            return self._json(401, {"error": "invalid_key"})
        if not allowed:
            return self._json(402, {"error": "payment_required", "message": "out of API calls",
                                    "buy_url": CFG["buy_url"], "calls_remaining": 0})
        t0 = time.perf_counter()
        try:
            data = CLIENT.run(**params)
        except TypeError as e:
            LEDGER.refund(acct)
            return self._json(400, {"error": "bad_params", "message": str(e)})
        except Exception as e:
            LEDGER.refund(acct)
            return self._json(502, {"error": "upstream_failed", "message": f"{type(e).__name__}: {e}"})
        ms = round((time.perf_counter() - t0) * 1000, 1)
        STATS["charged"] += 1
        remaining = max(0, acct["purchased"] - acct["used"])
        if acct.get("customer") and CFG["stripe_key"]:
            METER_Q.put((acct["customer"], acct["used"]))
        return self._json(200, {
            "data": data,
            "meta": {"ms": ms, "charged": True, "calls_remaining": remaining,
                     "demo_key": bool(acct.get("demo")), "api": CFG["name"]},
        }, {"X-Unclick-Calls-Remaining": str(remaining)})


def main() -> None:
    if CFG["stripe_key"]:
        threading.Thread(target=meter_worker, daemon=True).start()
    port = int(os.environ.get("PORT") or CFG["port"])
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"unclick api '{CFG['name']}' on 0.0.0.0:{port} billing={'on' if CFG['stripe_key'] else 'off'}"
          f" client={'ok' if not CLIENT_ERROR else 'ERROR'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
