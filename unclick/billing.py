"""Stripe test-mode billing for published Unclick APIs: prepaid call packs + a usage meter.

Mount in app/main.py:
    from unclick import billing
    app.include_router(billing.router)          # routes under /api/billing
    billing.add_listener(sink.emit)             # optional: billing events -> UI (EventSink.emit shape)

Design (prepaid credits):
  * Catalog, created idempotently on first use: one-time Price lookup_key "unclick_calls_100"
    ($5 for 100 calls, env UNCLICK_PACK_CENTS / UNCLICK_PACK_CALLS) and a Billing Meter with
    event_name "unclick_api_call" (sum of payload.value, customer from payload.stripe_customer_id).
  * POST /api/billing/checkout {api_id, key?} -> Stripe Checkout (mode=payment, test mode)
    -> {checkout_url}. Passing an existing key tops up that key's customer instead.
  * GET /api/billing/success?session_id=... verifies payment_status == "paid", creates the
    API key (or tops up), and 303-redirects to
        {PUBLIC_BASE_URL}/?paid=1&api_id=<api_id>#unclick_key=<key>
    (key in the fragment, so it never hits server logs). Reloading is idempotent.
  * GET /api/billing/usage?key=... (or header X-Unclick-Key) -> {calls_purchased, calls_used,
    calls_remaining}.

How keys reach the sandbox: they don't have to. A key is  uk_<customer id>_<32 hex>. The
Stripe Customer holds metadata unclick_key_sha256, unclick_calls_purchased and
unclick_calls_used. The sandbox server (unclick/api_template/server.py) parses the customer
id out of the key, does one GET /v1/customers/{id}, checks the hash and caches it. So keys
bought after publishing work at once, and top-ups are seen on the next 402 check. The
sandbox only needs the Stripe key (config file written at publish time; egress
api.stripe.com). Set STRIPE_SANDBOX_KEY to a restricted rk_test_ key (Customers write,
Billing meter events write) to avoid putting the full sk_test_ key in the sandbox.

The demo key (issue_demo_key) belongs to one "Unclick demo" customer; the sandbox checks it
locally with a fixed allowance, so the live demo never blocks, and still meters its calls.

Local store: ~/.unclick/billing.json (env UNCLICK_DATA_DIR), outside the repo: maps Checkout
session -> key so /success reloads return the same key, plus the demo customer id.
Only sk_test_/rk_test_ keys are accepted; live keys raise. Keys are never logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

try:
    from dotenv import load_dotenv

    load_dotenv()  # does not override variables already set
except ImportError:  # pragma: no cover
    pass

try:
    import stripe
except ImportError:  # pragma: no cover - stripe is in requirements.txt
    stripe = None  # type: ignore[assignment]

log = logging.getLogger("unclick.billing")

METER_EVENT = "unclick_api_call"
PRICE_LOOKUP = "unclick_calls_100"
KEY_HEADER = "X-Unclick-Key"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def pack_calls() -> int:
    return int(_env("UNCLICK_PACK_CALLS", "100"))


def pack_cents() -> int:
    return int(_env("UNCLICK_PACK_CENTS", "500"))


def public_base_url() -> str:
    return _env("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")


class BillingDisabled(RuntimeError):
    """No usable Stripe test key configured."""


def _check_test_key(key: str, what: str) -> str:
    if not key:
        raise BillingDisabled(f"{what} is not set")
    if not (key.startswith("sk_test_") or key.startswith("rk_test_")):
        raise BillingDisabled(f"{what} is not a Stripe TEST key (sk_test_/rk_test_); refusing to run")
    return key


def stripe_key() -> str:
    return _check_test_key(_env("STRIPE_SECRET_KEY"), "STRIPE_SECRET_KEY")


def sandbox_stripe_key() -> str:
    """Key shipped into the API sandbox. Prefer a restricted key if provided."""
    if _env("STRIPE_SANDBOX_KEY"):
        return _check_test_key(_env("STRIPE_SANDBOX_KEY"), "STRIPE_SANDBOX_KEY")
    return stripe_key()


def enabled() -> bool:
    try:
        stripe_key()
        return stripe is not None
    except BillingDisabled:
        return False


def _d(obj: Any) -> dict[str, Any]:
    """stripe>=12 objects are not dicts; normalise (mocks may already pass dicts)."""
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _stripe():
    if stripe is None:
        raise BillingDisabled("stripe package not installed")
    stripe.api_key = stripe_key()
    return stripe


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

_listeners: list[Callable[..., None]] = []


def add_listener(fn: Callable[..., None]) -> None:
    """fn(kind, message, **data), i.e. an EventSink.emit. Gets kind="billing" events."""
    _listeners.append(fn)


def _emit(message: str, **data: Any) -> None:
    for fn in list(_listeners):
        try:
            fn("billing", message, **data)
        except Exception:  # a broken UI listener must not break billing
            log.exception("billing listener failed")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.path = Path(_env("UNCLICK_DATA_DIR") or Path.home() / ".unclick") / "billing.json"
        self.data: dict[str, Any] = {"sessions": {}, "demo_customer": None}
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                log.warning("could not write %s; keys kept in memory only", self.path)


_store: _Store | None = None


def store() -> _Store:
    global _store
    if _store is None:
        _store = _Store()
    return _store


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def make_key(customer_id: str) -> str:
    return f"uk_{customer_id}_{secrets.token_hex(16)}"


def parse_key(key: str) -> str | None:
    """uk_cus_XXXX_<hex> -> cus_XXXX. Same rule as api_template/server.py."""
    if not key.startswith("uk_cus_"):
        return None
    head, _, secret = key.rpartition("_")
    if len(secret) < 16 or not head.startswith("uk_cus_"):
        return None
    return head[3:]


def _customer_for_key(key: str):
    cus = parse_key(key or "")
    if not cus:
        raise HTTPException(401, "invalid key")
    s = _stripe()
    try:
        c = _d(s.Customer.retrieve(cus))
    except Exception:
        raise HTTPException(401, "invalid key") from None
    md = dict(c.get("metadata") or {})
    if c.get("deleted") or md.get("unclick_key_sha256") != sha256(key):
        raise HTTPException(401, "invalid key")
    return c, md


# ---------------------------------------------------------------------------
# Catalog (idempotent)
# ---------------------------------------------------------------------------

_catalog: dict[str, str] = {}
_catalog_lock = threading.Lock()


def ensure_catalog() -> dict[str, str]:
    """Create or find the Price (by lookup_key) and the Meter (by event_name). Cached."""
    with _catalog_lock:
        if _catalog:
            return _catalog
        s = _stripe()
        prices = _d(s.Price.list(lookup_keys=[PRICE_LOOKUP], active=True, limit=1))["data"]
        if prices:
            price = prices[0]
        else:
            price = _d(s.Price.create(
                unit_amount=pack_cents(), currency="usd", lookup_key=PRICE_LOOKUP,
                product_data={"name": f"Unclick API calls ({pack_calls()} calls)"},
                metadata={"unclick_calls": str(pack_calls())},
            ))
        meter_id = None
        for m in map(_d, s.billing.Meter.list(limit=100).auto_paging_iter()):
            if m.get("event_name") == METER_EVENT:
                meter_id = m["id"]
                if m.get("status") == "inactive":
                    s.billing.Meter.reactivate(meter_id)
                break
        if meter_id is None:
            meter_id = _d(s.billing.Meter.create(
                display_name="Unclick API calls",
                event_name=METER_EVENT,
                default_aggregation={"formula": "sum"},
                customer_mapping={"type": "by_id", "event_payload_key": "stripe_customer_id"},
                value_settings={"event_payload_key": "value"},
            ))["id"]
        calls = int((price.get("metadata") or {}).get("unclick_calls") or pack_calls())
        _catalog.update(price_id=price["id"], meter_id=meter_id, calls=str(calls),
                        amount_cents=str(price.get("unit_amount") or pack_cents()))
        log.info("billing catalog ready: price %s, meter %s", price["id"], meter_id)
        return _catalog


def try_ensure_catalog() -> None:
    """Startup hook: never crash the app if Stripe is missing or down."""
    if not enabled():
        log.warning("Stripe billing disabled (no STRIPE_SECRET_KEY test key)")
        return
    try:
        ensure_catalog()
    except Exception as e:
        log.warning("Stripe catalog setup failed, will retry on first checkout: %s", e)


# ---------------------------------------------------------------------------
# Used by unclick/publish.py
# ---------------------------------------------------------------------------


def issue_demo_key(calls: int = 100) -> str:
    """Fresh free key for the live demo. Metered to one shared demo customer.

    Without Stripe it returns a local-only key (uk_demo_...) the sandbox still accepts.
    """
    if not enabled():
        return f"uk_demo_{secrets.token_hex(16)}"
    s = _stripe()
    st = store()
    with st.lock:
        cus = st.data.get("demo_customer")
        if cus:
            try:
                if _d(s.Customer.retrieve(cus)).get("deleted"):
                    cus = None
            except Exception:
                cus = None
        if not cus:
            cus = _d(s.Customer.create(name="Unclick demo", description="Free demo key for live Unclick APIs",
                                       metadata={"unclick_demo": "1"}))["id"]
            st.data["demo_customer"] = cus
            st.save()
    key = make_key(cus)
    s.Customer.modify(cus, metadata={"unclick_key_sha256": sha256(key),
                                     "unclick_calls_purchased": str(calls), "unclick_calls_used": "0"})
    return key


def sandbox_config() -> dict[str, Any]:
    """Billing part of the sandbox server's config.json. Contains the Stripe key: write it
    only into the sandbox (chmod 600), never log it."""
    cfg: dict[str, Any] = {"meter_event": METER_EVENT, "stripe_key": ""}
    if enabled():
        cfg["stripe_key"] = sandbox_stripe_key()
        try:
            ensure_catalog()
        except Exception as e:
            log.warning("catalog setup failed during publish: %s", e)
    return cfg


def buy_url(api_id: str) -> str:
    """Where the sandbox's 402 / docs page sends buyers (the web UI handles ?buy=)."""
    return f"{public_base_url()}/?buy={quote(api_id)}"


def usage(key: str) -> dict[str, int]:
    _c, md = _customer_for_key(key)
    bought = int(md.get("unclick_calls_purchased") or 0)
    used = int(md.get("unclick_calls_used") or 0)
    return {"calls_purchased": bought, "calls_used": used, "calls_remaining": max(0, bought - used)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/billing", tags=["billing"])

# Warm the catalog in the background at import (newer Starlette has no router on_startup);
# every route also calls ensure_catalog() lazily, so this is only a head start.
threading.Thread(target=try_ensure_catalog, daemon=True, name="unclick-billing-catalog").start()


class CheckoutIn(BaseModel):
    api_id: str = ""        # the published API (its sandbox id); run_id is accepted as a fallback
    run_id: str | None = None
    key: str | None = None  # top up an existing key instead of buying a new one


def _disabled_guard() -> None:
    if not enabled():
        raise HTTPException(503, "Stripe billing is not configured (STRIPE_SECRET_KEY sk_test_...)")


@router.get("/config")
def get_config() -> dict[str, Any]:
    """For the UI: is billing on, what does a pack cost."""
    if not enabled():
        return {"enabled": False}
    try:
        cat = ensure_catalog()
    except Exception as e:
        return {"enabled": False, "error": str(e)[:200]}
    return {"enabled": True, "calls": int(cat["calls"]), "amount_cents": int(cat["amount_cents"]),
            "currency": "usd", "publishable_key": _env("STRIPE_PUBLISHABLE_KEY")}


@router.post("/checkout")
def create_checkout(body: CheckoutIn) -> dict[str, str]:
    _disabled_guard()
    if not body.api_id:
        body.api_id = body.run_id or "unclick"
    cat = ensure_catalog()
    s = _stripe()
    base = public_base_url()
    params: dict[str, Any] = dict(
        mode="payment",
        line_items=[{"price": cat["price_id"], "quantity": 1}],
        success_url=f"{base}/api/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/?api_id={quote(body.api_id)}&canceled=1",
        metadata={"api_id": body.api_id, "calls": cat["calls"]},
        payment_intent_data={"description": f"Unclick API calls for {body.api_id}"},
    )
    if body.key:
        c, _md = _customer_for_key(body.key)
        params["customer"] = c["id"]
        params["metadata"]["topup"] = "1"
    else:
        params["customer_creation"] = "always"
    sess = _d(s.checkout.Session.create(**params))
    _emit("Checkout session created", event="checkout_created", checkout_url=sess["url"],
          amount_cents=int(cat["amount_cents"]))
    return {"checkout_url": sess["url"], "session_id": sess["id"]}


def fulfill(session_id: str) -> tuple[str, str, int]:
    """Verify a paid Checkout session and return (key, api_id, calls_remaining). Idempotent."""
    s = _stripe()
    st = store()
    sess = _d(s.checkout.Session.retrieve(session_id))
    if sess.get("payment_status") != "paid":
        raise HTTPException(402, "payment not completed")
    md = dict(sess.get("metadata") or {})
    api_id = md.get("api_id", "")
    calls = int(md.get("calls") or pack_calls())
    cus = sess.get("customer")
    if not cus:
        raise HTTPException(500, "checkout session has no customer")
    with st.lock:
        done = st.data["sessions"].get(session_id)
        if done:
            return done["key"], api_id, int(done.get("remaining", calls))
        cmd = dict(_d(s.Customer.retrieve(cus)).get("metadata") or {})
        bought = int(cmd.get("unclick_calls_purchased") or 0) + calls
        used = int(cmd.get("unclick_calls_used") or 0)
        new_md = {"unclick_calls_purchased": str(bought), "unclick_api_id": api_id}
        existing = None
        if md.get("topup"):
            existing = next((v["key"] for v in st.data["sessions"].values()
                             if parse_key(v["key"]) == cus and sha256(v["key"]) == cmd.get("unclick_key_sha256")), None)
        if existing:
            key = existing
        else:
            key = make_key(cus)
            new_md["unclick_key_sha256"] = sha256(key)
            new_md["unclick_calls_used"] = str(used)
        s.Customer.modify(cus, metadata=new_md)
        remaining = max(0, bought - used)
        st.data["sessions"][session_id] = {"key": key, "api_id": api_id, "remaining": remaining, "ts": time.time()}
        st.save()
    _emit("Payment received, API key issued", event="paid", api_key=key, calls_remaining=remaining,
          amount_cents=int(sess.get("amount_total") or 0))
    return key, api_id, remaining


@router.get("/success")
def checkout_success(session_id: str = Query(...)) -> RedirectResponse:
    _disabled_guard()
    key, api_id, _remaining = fulfill(session_id)
    return RedirectResponse(f"{public_base_url()}/?paid=1&api_id={quote(api_id)}#unclick_key={quote(key)}",
                            status_code=303)


@router.get("/usage")
def get_usage(key: str | None = Query(None),
              x_unclick_key: str | None = Header(None, alias=KEY_HEADER)) -> dict[str, int]:
    _disabled_guard()
    k = x_unclick_key or key
    if not k:
        raise HTTPException(400, "pass ?key= or the X-Unclick-Key header")
    return usage(k)
