"""Unclick published API server. Runs INSIDE the API sandbox next to client.py.

Zero dependencies (stdlib only), so nothing needs pip and egress can stay locked to
[backend host, api.stripe.com]. It also boots instantly after a wake-on-request.

Routes (bind 0.0.0.0:8000):
  GET|POST /v1/run   query params + JSON body -> client.run(**params) -> JSON. Needs X-Unclick-Key.
  GET      /health   {"ok": true, ...}. No key, never charged (used for wake timing).
  GET      /         small HTML page: name, source site, example curl.
  GET      /docs     same page (PublishedApi.docs_url points here).
  GET      /openapi.json   OpenAPI 3.1: /v1/run params from client.run's signature, response = schema.json.
  GET      /schema.json    JSON Schema inferred at publish from the verified sample output.
  POST     /mcp      MCP Streamable HTTP (JSON-RPC, stateless): initialize, ping, tools/list, tools/call.
                     One tool (get_<name>); tools/call is authed + charged exactly like /v1/run.
                     Key: X-Unclick-Key header or ?key=.
  GET      /health/verify  re-runs client.run() uncharged, validates against schema.json:
                     {ok, checked_at, ms, rows, schema, problems[]}. Cached 15 s. No key.

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


SCHEMA_PATH = os.path.join(HERE, "schema.json")
SCHEMA: dict = {}
try:
    with open(SCHEMA_PATH, encoding="utf-8") as _f:
        SCHEMA = json.load(_f)
except (OSError, ValueError):
    SCHEMA = {}

_PY_TO_JSON = {bool: "boolean", int: "integer", float: "number", str: "string", list: "array", dict: "object"}


def run_params() -> list[dict]:
    """Named parameters of client.run as [{name, schema, required}] (from annotations / defaults)."""
    if CLIENT is None or CLIENT_ERROR:
        return []
    import inspect
    out = []
    try:
        sig = inspect.signature(CLIENT.run)
    except (TypeError, ValueError):
        return []
    docs = getattr(CLIENT, "PARAM_DOCS", None)
    docs = docs if isinstance(docs, dict) else {}
    examples = getattr(CLIENT, "EXAMPLE_PARAMS", None)
    examples = examples if isinstance(examples, dict) else {}
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = p.annotation
        args = [a for a in getattr(ann, "__args__", None) or () if a is not type(None)]
        if args and not isinstance(ann, type):  # int | None, Optional[int]
            ann = args[0]
        if isinstance(ann, str):  # from __future__ import annotations
            ann = {"bool": bool, "int": int, "float": float, "str": str, "list": list, "dict": dict}.get(
                ann.split("|")[0].strip(), inspect.Parameter.empty)
        if ann is inspect.Parameter.empty and p.default is not inspect.Parameter.empty and p.default is not None:
            ann = type(p.default)
        schema: dict = {"type": _PY_TO_JSON.get(ann, "string")}
        if p.default is not inspect.Parameter.empty and isinstance(p.default, (bool, int, float, str)):
            schema["default"] = p.default
        if isinstance(docs.get(p.name), str):
            schema["description"] = docs[p.name]
        if isinstance(examples.get(p.name), (bool, int, float, str)):
            schema["examples"] = [examples[p.name]]
        out.append({"name": p.name, "schema": schema, "required": p.default is inspect.Parameter.empty,
                    "description": schema.get("description", "")})
    return out


def typed_params(params: dict) -> dict:
    """Query-string values are guessed by coerce(); fix them up to run()'s declared types (q=2024 stays a str)."""
    types = {p["name"]: p["schema"].get("type") for p in run_params()}
    out = {}
    for k, v in params.items():
        t = types.get(k)
        if t == "string" and not isinstance(v, str):
            v = str(v).lower() if isinstance(v, bool) else str(v)
        elif t == "number" and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        out[k] = v
    return out


def tool_name() -> str:
    slug = "".join(c if c.isalnum() else "_" for c in CFG["name"].lower()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return ("get_" + (slug or "data"))[:64]


def tool_description() -> str:
    task = (CFG.get("task") or "").strip()
    site = CFG.get("source_site") or "a website"
    desc = f"Live data from {site}"
    if task:
        desc += f": {task}"
    names = [p["name"] for p in run_params()]
    if names:
        desc += ". Parameters: " + ", ".join(names)
    return desc + ". Returns JSON (verified against what the site shows on screen)."


def _jtype_ok(v, types) -> bool:
    types = types if isinstance(types, list) else [types]
    for t in types:
        if t == "null" and v is None:
            return True
        if t == "boolean" and isinstance(v, bool):
            return True
        if t == "integer" and isinstance(v, int) and not isinstance(v, bool):
            return True
        if t == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return True
        if t == "string" and isinstance(v, str):
            return True
        if t == "array" and isinstance(v, list):
            return True
        if t == "object" and isinstance(v, dict):
            return True
    return False


def validate(data, schema: dict, path: str = "$", problems: list | None = None, depth: int = 0) -> list:
    """Small JSON Schema check: type, required, properties, items (first 50). Returns problem strings."""
    problems = [] if problems is None else problems
    if len(problems) >= 20 or depth > 6 or not schema:
        return problems
    t = schema.get("type")
    if t and not _jtype_ok(data, t):
        problems.append(f"{path}: expected {t}, got {type(data).__name__}")
        return problems
    if isinstance(data, dict):
        for k in schema.get("required", []):
            if data.get(k) is None:
                problems.append(f"{path}: missing required field '{k}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in data:
                validate(data[k], sub, f"{path}.{k}", problems, depth + 1)
    elif isinstance(data, list) and schema.get("items"):
        for i, item in enumerate(data[:50]):
            validate(item, schema["items"], f"{path}[{i}]", problems, depth + 1)
    return problems


MCP_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
VERIFY_TTL = 15  # seconds: /health/verify is free, so repeated hits reuse the last check
_VERIFY: dict = {"at": 0.0, "result": None}
_VERIFY_LOCK = threading.Lock()


def verify_now() -> dict:
    """Re-run client.run() (uncharged) and check the output against schema.json."""
    with _VERIFY_LOCK:
        if _VERIFY["result"] is not None and time.time() - _VERIFY["at"] < VERIFY_TTL:
            return dict(_VERIFY["result"], cached=True)
        problems: list = []
        rows = None
        t0 = time.perf_counter()
        if CLIENT is None or CLIENT_ERROR:
            problems.append("client.py failed to import: " + CLIENT_ERROR[-300:])
        else:
            try:
                data = CLIENT.run()
                if data is None or data == [] or data == {}:
                    problems.append("client.run() returned no data")
                else:
                    rows = len(data) if isinstance(data, (list, dict)) else 1
                    validate(data, SCHEMA, problems=problems)
            except Exception as e:
                problems.append(f"client.run() raised {type(e).__name__}: {str(e)[:300]}")
        result = {
            "ok": not problems, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ms": round((time.perf_counter() - t0) * 1000, 1), "rows": rows,
            "schema": bool(SCHEMA), "problems": problems, "api": CFG["name"], "cached": False,
        }
        _VERIFY.update(at=time.time(), result=result)
        return result


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
<li><code>POST /mcp</code> MCP server (Streamable HTTP) with one tool, <code>{tool}</code>; same key, same price</li>
<li><a href="/openapi.json"><code>GET /openapi.json</code></a> OpenAPI 3.1 spec with the typed response schema</li>
<li><a href="/health/verify"><code>GET /health/verify</code></a> re-run the client and validate it against the schema (free)</li>
<li><code>GET /health</code> liveness (free)</li></ul>
<h3>Use it from Claude Desktop / Cursor</h3><pre>{mcp}</pre></body></html>"""


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
        if path == "/openapi.json":
            return self._json(200, self._openapi())
        if path == "/schema.json":
            return self._json(200, SCHEMA)
        if path == "/health/verify":
            return self._json(200, verify_now())
        if path == "/mcp":
            return self._mcp(url.query)
        return self._json(404, {"error": "not_found",
                                "routes": ["/v1/run", "/mcp", "/openapi.json", "/health", "/health/verify", "/"]})

    def _base_url(self) -> str:
        host = self.headers.get("Host", "localhost:8000")
        proto = self.headers.get("X-Forwarded-Proto")
        scheme = proto or ("http" if host.startswith(("localhost", "127.")) else "https")
        return f"{scheme}://{host}"

    def _openapi(self) -> dict:
        params = [{"name": p["name"], "in": "query", "required": p["required"], "schema": p["schema"],
                   **({"description": p["description"]} if p["description"] else {})}
                  for p in run_params()]
        data_schema = SCHEMA or {}
        ok_schema = {"type": "object", "required": ["data", "meta"], "properties": {
            "data": data_schema,
            "meta": {"type": "object", "properties": {
                "ms": {"type": "number"}, "charged": {"type": "boolean"},
                "calls_remaining": {"type": "integer"}, "demo_key": {"type": "boolean"},
                "api": {"type": "string"}}}}}
        err = {"type": "object", "properties": {"error": {"type": "string"}, "message": {"type": "string"}}}

        def resp(desc: str, schema: dict) -> dict:
            return {"description": desc, "content": {"application/json": {"schema": schema}}}

        run_op = {
            "operationId": tool_name(), "summary": tool_description(), "parameters": params,
            "security": [{"UnclickKey": []}],
            "responses": {"200": resp("Data from the site (charged 1 call)", ok_schema),
                          "400": resp("Bad parameters", err), "401": resp("Missing or invalid key", err),
                          "402": resp("Out of prepaid calls", err), "502": resp("Upstream site failed", err)},
        }
        return {
            "openapi": "3.1.0",
            "info": {"title": CFG["name"], "version": "1.0.0",
                     "description": f"Generated by Unclick from {CFG['source_site'] or 'a website'}. "
                                    "Paid per call (Stripe test mode). Also available as an MCP server at /mcp."},
            "servers": [{"url": self._base_url()}],
            "paths": {
                "/v1/run": {"get": run_op},
                "/health": {"get": {"operationId": "health", "summary": "Liveness (free)",
                                    "responses": {"200": {"description": "ok"}}}},
                "/health/verify": {"get": {
                    "operationId": "verify", "summary": "Re-run the client and validate output against the schema (free)",
                    "responses": {"200": resp("Verification result", {"type": "object", "properties": {
                        "ok": {"type": "boolean"}, "checked_at": {"type": "string", "format": "date-time"},
                        "problems": {"type": "array", "items": {"type": "string"}}}})}}},
            },
            "components": {
                "schemas": {"Data": data_schema},
                "securitySchemes": {"UnclickKey": {"type": "apiKey", "in": "header", "name": KEY_HEADER}},
            },
        }

    def _key(self, query: str = "") -> str:
        key = (self.headers.get(KEY_HEADER) or "").strip()
        if not key and query:
            key = (dict(urllib.parse.parse_qsl(query)).get("key") or "").strip()
        return key

    def _charged_call(self, key: str, params: dict) -> tuple[int, dict, dict]:
        """Auth + charge + client.run. Returns (status, body, headers). Failed calls are refunded."""
        if not key:
            return 401, {"error": "missing_key", "message": f"send header {KEY_HEADER}"}, {}
        if CLIENT is None or CLIENT_ERROR:
            return 503, {"error": "client_unavailable", "message": CLIENT_ERROR[-500:]}, {}
        allowed, acct = LEDGER.charge(key)
        if acct is None:
            return 401, {"error": "invalid_key"}, {}
        if not allowed:
            return 402, {"error": "payment_required", "message": "out of API calls",
                         "buy_url": CFG["buy_url"], "calls_remaining": 0}, {}
        t0 = time.perf_counter()
        try:
            data = CLIENT.run(**typed_params(params))
        except TypeError as e:
            LEDGER.refund(acct)
            return 400, {"error": "bad_params", "message": str(e)}, {}
        except Exception as e:
            LEDGER.refund(acct)
            return 502, {"error": "upstream_failed", "message": f"{type(e).__name__}: {e}"}, {}
        ms = round((time.perf_counter() - t0) * 1000, 1)
        STATS["charged"] += 1
        remaining = max(0, acct["purchased"] - acct["used"])
        if acct.get("customer") and CFG["stripe_key"]:
            METER_Q.put((acct["customer"], acct["used"]))
        return 200, {
            "data": data,
            "meta": {"ms": ms, "charged": True, "calls_remaining": remaining,
                     "demo_key": bool(acct.get("demo")), "api": CFG["name"]},
        }, {"X-Unclick-Calls-Remaining": str(remaining)}

    # -- MCP (Streamable HTTP transport, JSON responses, stateless) ---------------------------

    def _mcp(self, query: str):
        if self.command != "POST":
            return self._json(405, {"error": "method_not_allowed", "message": "POST JSON-RPC to /mcp"},
                              {"Allow": "POST"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            msg = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self._json(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "parse error"}})
        key = self._key(query)
        if isinstance(msg, list):
            out = [r for r in (self._mcp_one(m, key) for m in msg) if r is not None]
            return self._json(200, out) if out else self._send(202, b"", "application/json")
        res = self._mcp_one(msg, key)
        if res is None:
            return self._send(202, b"", "application/json")
        return self._json(200, res)

    def _mcp_one(self, msg, key: str):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {"jsonrpc": "2.0", "id": (msg or {}).get("id") if isinstance(msg, dict) else None,
                    "error": {"code": -32600, "message": "invalid request"}}
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if mid is None:  # notification (notifications/initialized, cancelled, ...)
            return None

        def ok(result):
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        if method == "initialize":
            ver = params.get("protocolVersion")
            return ok({
                "protocolVersion": ver if ver in MCP_VERSIONS else MCP_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": CFG["name"], "title": f"{CFG['name']} (Unclick)", "version": "1.0.0"},
                "instructions": tool_description() + f" Each tools/call is charged 1 prepaid call; send header "
                                                     f"{KEY_HEADER} (or ?key=) with your Unclick key.",
            })
        if method == "ping":
            return ok({})
        if method == "tools/list":
            props = {p["name"]: p["schema"] for p in run_params()}
            req = [p["name"] for p in run_params() if p["required"]]
            schema: dict = {"type": "object", "properties": props}
            if req:
                schema["required"] = req
            return ok({"tools": [{"name": tool_name(), "title": CFG["name"], "description": tool_description(),
                                  "inputSchema": schema,
                                  "annotations": {"readOnlyHint": True, "openWorldHint": True}}]})
        if method == "tools/call":
            if params.get("name") != tool_name():
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
            STATS["calls"] += 1
            args = params.get("arguments") or {}
            status, body, _ = self._charged_call(key, args if isinstance(args, dict) else {})
            if status != 200:
                return ok({"content": [{"type": "text", "text": json.dumps(body)}], "isError": True})
            text = json.dumps(body["data"], ensure_ascii=False, default=str)
            return ok({"content": [{"type": "text", "text": text}], "isError": False,
                       "_meta": {"unclick/calls_remaining": body["meta"]["calls_remaining"],
                                 "unclick/ms": body["meta"]["ms"]}})
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}

    def _page(self) -> str:
        base = self._base_url()
        demo = CFG.get("demo_key") or "<your key>"
        curl = f'curl -H "{KEY_HEADER}: {demo}" "{base}/v1/run"'
        site = CFG["source_site"] or "#"
        buy = f' (<a href="{html.escape(CFG["buy_url"])}">buy calls</a>)' if CFG["buy_url"] else ""
        mcp = json.dumps({"mcpServers": {CFG["name"]: {"url": base + "/mcp", "headers": {KEY_HEADER: demo}}}},
                         indent=2)
        return PAGE.format(name=html.escape(CFG["name"]), site=html.escape(site),
                           site_text=html.escape(CFG["source_site"] or "a website"),
                           curl=html.escape(curl), buy=buy, tool=html.escape(tool_name()), mcp=html.escape(mcp))

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
        params.pop("key", None)  # ?key= is auth, not a client parameter
        status, body, headers = self._charged_call(self._key(query), params)
        return self._json(status, body, headers)


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
