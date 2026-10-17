"""Unclick pipeline: boot -> explore -> discover -> code + verify (self-fixing) -> publish.

`run(url, task, sink)` is called by app/main.py and scripts/run_agent.py. Every step reports through the EventSink
with the kinds/fields defined in unclick/contracts.py.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import traceback
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

from unclick import prompts
from unclick.contracts import CapturedRequest, Desktop, EventSink, RunResult, Sandbox, SandboxFactory

WORKDIR = "/tmp/unclick"
CLIENT_PATH = f"{WORKDIR}/client.py"
MAX_EXPLORE_STEPS = int(os.getenv("UNCLICK_MAX_STEPS", "15"))
EXPLORE_BUDGET_S = float(os.getenv("UNCLICK_EXPLORE_BUDGET_S", "120"))
MIN_OVERLAP = int(os.getenv("UNCLICK_MIN_OVERLAP", "8"))
MAX_DONE_REJECTIONS = 3
SLOT_WAIT_S = float(os.getenv("UNCLICK_SLOT_WAIT_S", "120"))
MAX_ATTEMPTS = int(os.getenv("UNCLICK_MAX_ATTEMPTS", "5"))
OPEN_WAIT_S = float(os.getenv("UNCLICK_OPEN_WAIT_S", "2"))
ACTION_WAIT_S = float(os.getenv("UNCLICK_ACTION_WAIT_S", "1.5"))
SCREENSHOT_EVERY_S = 1.5
DESKTOP_SHAPE = os.getenv("UNCLICK_DESKTOP_SHAPE", "s-2vcpu-4gb")  # fakes only; desktop.py picks its own
RUNNER_SHAPE = os.getenv("UNCLICK_RUNNER_SHAPE", "s-1vcpu-1gb")
PIP_EGRESS = ["pypi.org", "*.pythonhosted.org"]

NOISE = re.compile(r"(_vercel|insights|analytics|telemetry|sentry|segment|mixpanel|amplitude|hotjar|clarity|"
                   r"google-analytics|googletagmanager|doubleclick|/collect|beacon|/v1/(event|logs|traces)|"
                   r"clerk|/auth|session|csrf|feature|flags?\b|launchdarkly|statsig|intercom|stripe\.com|"
                   r"/_next/static|favicon|manifest\.json|\.js(\?|$)|\.css(\?|$))", re.I)
DROP_HEADERS = re.compile(r"^(cookie|authorization|proxy-authorization|host|content-length|connection|"
                          r"accept-encoding|sec-.*|:.*|priority|if-none-match|if-modified-since)$", re.I)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _site_egress(url: str) -> list[str]:
    host = _host(url)
    parts = host.split(".")
    base = ".".join(parts[-2:]) if len(parts) >= 2 else host
    rules = [host, f"*.{base}"]
    if base != host:
        rules.append(base)
    rules += [r.strip() for r in os.getenv("UNCLICK_EXTRA_EGRESS", "").split(",") if r.strip()]
    return list(dict.fromkeys(rules))


def _egress_allows(rules: list[str], host: str) -> bool:
    if not rules:
        return True  # [] means open
    for r in rules:
        r = r.split(":")[0].lower() if not r.startswith("[") else r
        if r == "*" or r == host:
            return True
        if r.startswith("*.") and (host.endswith(r[1:]) or host == r[2:]):
            return True
    return False


def _try_json(body: str) -> Any:
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return None


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9'._-]{2,}", text.lower()) if not t.isdigit() or len(t) >= 2}


def _string_values(data: Any, out: list[str], limit: int = 4000) -> list[str]:
    if len(out) >= limit:
        return out
    if isinstance(data, dict):
        for v in data.values():
            _string_values(v, out, limit)
    elif isinstance(data, list):
        for v in data:
            _string_values(v, out, limit)
    elif isinstance(data, (str, int, float)) and not isinstance(data, bool):
        out.append(str(data))
    return out


def _is_jsonish(c: CapturedRequest) -> bool:
    if "json" in (c.content_type or "").lower():
        return True
    b = (c.response_body or "").lstrip()
    return b[:1] in ("{", "[") and _try_json(b) is not None


def _overlap(c: CapturedRequest, page_tokens: set[str]) -> int:
    """How many distinct on-screen tokens appear in the response's values (data JSON: hundreds; auth/config: ~0)."""
    data = _try_json(c.response_body or "")
    return len(_tokens(" ".join(_string_values(data, []))) & page_tokens) if data is not None else 0


def _relevant(c: CapturedRequest, page_tokens: set[str]) -> bool:
    return 200 <= (c.status or 0) < 300 and not NOISE.search(c.url) and _overlap(c, page_tokens) >= MIN_OVERLAP


def _score(c: CapturedRequest, page_tokens: set[str], task_tokens: set[str]) -> float:
    body = c.response_body or ""
    data = _try_json(body)
    s = 0.0
    if "json" in (c.content_type or "").lower():
        s += 3
    if data is not None:
        s += 2
        vals = _tokens(" ".join(_string_values(data, [])))
        s += min(len(vals & page_tokens), 60) / 4  # up to +15 for matching what is on screen
        keys = _tokens(" ".join(re.findall(r'"([A-Za-z_][\w]*)"\s*:', body[:20000])))
        s += min(len(keys & task_tokens), 3)
    s += min(math.log10(max(len(body), 1)), 6)
    if NOISE.search(c.url):
        s -= 10
    if not (200 <= (c.status or 0) < 300):
        s -= 6
    if (c.method or "GET").upper() == "GET":
        s += 0.5
    return round(s, 2)


def _structure(data: Any, depth: int = 0, key: str = "$") -> list[str]:
    pad = "  " * depth
    if depth > 4:
        return []
    if isinstance(data, dict):
        lines = [f"{pad}{key}: object keys={list(data.keys())[:25]}"]
        for k, v in list(data.items())[:25]:
            if isinstance(v, (dict, list)):
                lines += _structure(v, depth + 1, k)
        return lines
    if isinstance(data, list):
        lines = [f"{pad}{key}: list len={len(data)}"]
        if data:
            lines += _structure(data[0], depth + 1, f"{key}[0]")
        return lines
    return []


def _trim(data: Any, n: int = 2) -> Any:
    if isinstance(data, dict):
        return {k: _trim(v, n) for k, v in data.items()}
    if isinstance(data, list):
        return [_trim(v, n) for v in data[:n]] + ([f"... {len(data) - n} more"] if len(data) > n else [])
    if isinstance(data, str) and len(data) > 200:
        return data[:200] + "..."
    return data


def _cap_line(c: CapturedRequest) -> str:
    return f"{c.method} {c.url} -> {c.status} {c.content_type} {len(c.response_body or '')}B"


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).casefold())


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"\s*-?[\d,]+(\.\d+)?\s*", v):
        return float(v.replace(",", ""))
    return None


def _records(data: Any) -> list[dict]:
    """The main list of records in the client's output (largest list of dicts, searched recursively)."""
    best: list[dict] = []

    def walk(d: Any) -> None:
        nonlocal best
        if isinstance(d, list):
            dicts = [x for x in d if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
            for x in d[:50]:
                walk(x)
        elif isinstance(d, dict):
            for v in d.values():
                walk(v)
    walk(data)
    if not best and isinstance(data, dict):
        best = [data]
    return best


def _flat_values(rec: Any) -> list[Any]:
    return _string_values(rec, [], 500) if isinstance(rec, (dict, list)) else [rec]


def _bad_url_fields(recs: list[dict]) -> list[str]:
    keys = {k for r in recs[:20] if isinstance(r, dict) for k in r if re.search(r"url|link|href|website", k, re.I)}
    bad = []
    for k in sorted(keys):
        vals = [r.get(k) for r in recs[:20] if isinstance(r, dict) and r.get(k) not in (None, "")]
        urlish = [v for v in vals if isinstance(v, str) and re.match(r"^(https?://|/|www\.)|^[\w-]+(\.[\w-]+)+(/|$)", v)]
        if vals and len(urlish) < 0.8 * len(vals):
            bad.append(k)
    return bad


def verify_output(stdout: str, expected: list[dict[str, Any]]) -> tuple[bool, str, str]:
    """Check the client's stdout against ground truth read off the screen. Returns (ok, got, notes)."""
    data = _try_json(stdout.strip())
    if data is None:
        return False, stdout[:800], "stdout is not valid JSON. Print only json.dumps(run()) when run as main."
    recs = _records(data)
    got = json.dumps(recs[:5], ensure_ascii=False)[:1500]
    if not recs:
        return False, got, "Output has no records (expected a list of objects)."
    bad_url_keys = _bad_url_fields(recs)
    if bad_url_keys:
        return False, got, (f"Fields {bad_url_keys} are named like URLs but most values are not URLs. Use the item's own "
                            "URL field from the response (absolute http(s) URL), or omit the field if there is none.")
    if not expected:
        return True, got, f"{len(recs)} records; no on-screen ground truth was extractable, accepted on shape only."

    missing = []
    for item in expected:
        strs = [_norm(v) for v in item.values() if _as_number(v) is None and isinstance(v, str) and _norm(v)]
        nums = [_as_number(v) for v in item.values() if _as_number(v) is not None]
        found = False
        for rec in recs:
            vals = _flat_values(rec)
            blob = "|".join(_norm(v) for v in vals)
            rec_nums = [n for n in (_as_number(v) for v in vals) if n is not None]
            if not all(s in blob for s in strs):
                continue
            # Live sites move (votes tick up), so allow a small drift on numbers.
            if all(any(abs(n - rn) <= max(3, 0.1 * abs(n)) for rn in rec_nums) for n in nums):
                found = True
                break
        if not found:
            missing.append(item)
    # Screen-text extraction sometimes pairs a title with its neighbour's number. One such item is tolerated when
    # every value of it is still present in the output (just on different records) and the rest match exactly.
    all_vals = [v for rec in recs for v in _flat_values(rec)]
    all_blob = "|".join(_norm(v) for v in all_vals)
    all_nums = [n for n in (_as_number(v) for v in all_vals) if n is not None]

    def split(item: dict) -> bool:
        strs = [_norm(v) for v in item.values() if _as_number(v) is None and isinstance(v, str) and _norm(v)]
        nums = [_as_number(v) for v in item.values() if _as_number(v) is not None]
        return all(s in all_blob for s in strs) and all(any(abs(n - rn) <= max(3, 0.1 * abs(n)) for rn in all_nums)
                                                       for n in nums)
    if len(missing) == 1 and len(expected) >= 3 and split(missing[0]):
        return True, got, (f"{len(expected) - 1}/{len(expected)} on-screen items match exactly; one item's values "
                           f"are all present but on neighbouring records (screen-text pairing): "
                           f"{json.dumps(missing[0], ensure_ascii=False)[:300]}")
    if missing:
        return False, got, (f"{len(expected) - len(missing)}/{len(expected)} on-screen items found. Missing: "
                            f"{json.dumps(missing, ensure_ascii=False)[:800]}. Output has {len(recs)} records; "
                            f"first record keys: {list(recs[0].keys())[:20] if isinstance(recs[0], dict) else '?'}")
    return True, got, f"All {len(expected)} on-screen items found in {len(recs)} records."


def hardcoded_values(code: str, expected: list[dict[str, Any]], inputs: list[str] | tuple = ()) -> list[str]:
    """String values from the screen that appear verbatim in the code (a client faking its output).
    `inputs` are the query values the user/site sent (e.g. q=http): a parameter default equal to one is not faking."""
    ok = {_norm(v) for v in inputs if _norm(v)}
    vals = {str(v) for item in expected for v in item.values()
            if isinstance(v, str) and len(v.strip()) >= 4 and _norm(v) not in ok}
    return sorted(v for v in vals if v in code)


CONTROLS_JS = r"""(() => {
  const dpr = window.devicePixelRatio || 1;
  const bx = (window.outerWidth - window.innerWidth) / 2, by = window.outerHeight - window.innerHeight;
  const sel = 'select, button, [role=tab], [role=button], input[type=search], input[type=text], input:not([type]), a';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const off = r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth;
    if (r.width < 4 || r.height < 4 || (off && tag !== 'select')) continue;  // dropdowns work offscreen too
    let text = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || '').trim().replace(/\s+/g, ' ');
    if (tag === 'a' && !/next|more|load|page|›|»/i.test(text)) continue;  // skip plain links
    if (tag === 'select') {
      const lab = (el.labels && el.labels[0] && el.labels[0].innerText) || el.id || el.name || '';
      const opts = [...el.options].slice(0, 8).map(o => o.text.trim());
      text = `${lab} [current: ${el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''}] options: ${opts.join(' | ')}${el.options.length > 8 ? ' | ...' : ''}`;
    }
    el.dataset.unclickIdx = String(i);
    out.push({i, tag, text: text.slice(0, 160), x: Math.round((screenX + bx + r.left + r.width / 2) * dpr),
              y: Math.round((screenY + by + r.top + r.height / 2) * dpr)});
    if (++i >= 25) break;
  }
  return out;
})()"""

SELECT_JS = r"""((idx, want) => {
  const el = document.querySelector(`[data-unclick-idx="${idx}"]`);
  if (!el || el.tagName !== 'SELECT') return 'error: control ' + idx + ' is not a dropdown';
  const w = String(want).trim().toLowerCase();
  const opt = [...el.options].find(o => o.value.toLowerCase() === w || o.text.trim().toLowerCase() === w)
           || [...el.options].find(o => o.text.toLowerCase().includes(w));
  if (!opt) return 'error: no option matching ' + want;
  if (el.dataset.unclickOrig === undefined) el.dataset.unclickOrig = el.value;
  el.focus(); el.value = opt.value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return 'selected ' + opt.text.trim();
})"""


CHANGED_JS = r"""[...document.querySelectorAll('select[data-unclick-orig]')]
  .filter(el => el.value !== el.dataset.unclickOrig)
  .map(el => `dropdown #${el.dataset.unclickIdx}: set it back to '${[...el.options].find(o => o.value === el.dataset.unclickOrig)?.text.trim()}'`)"""


def _changed_filters(desktop: Any) -> list[str]:
    """Dropdowns the agent changed and has not restored (the screen would show a filtered view)."""
    try:
        return list(desktop.eval_js(CHANGED_JS) or []) if hasattr(desktop, "eval_js") else []
    except Exception:  # noqa: BLE001
        return []


BLOCKED_JS = r"""performance.getEntriesByType('resource')
  .filter(e => (e.initiatorType === 'fetch' || e.initiatorType === 'xmlhttprequest') && !e.responseStatus)
  .map(e => e.name)"""


def _blocked_hosts(desktop: Any, sb: Sandbox, page_url: str) -> list[str]:
    """Hosts the page's fetch/XHR calls failed to reach because egress does not allow them."""
    if not hasattr(desktop, "eval_js"):
        return []
    try:
        urls = desktop.eval_js(BLOCKED_JS) or []
        rules = sb.get_egress()
    except Exception:  # noqa: BLE001
        return []
    hosts = [_host(u) for u in urls if not NOISE.search(u)]
    return list(dict.fromkeys(h for h in hosts if h and h != _host(page_url) and not _egress_allows(rules, h)))


def _allow_host(sb: Sandbox, host: str, sink: EventSink, reason: str, wait: bool = True) -> None:
    """Emit the egress event first (the UI shows it at once), then apply; new hostnames take ~30 s to propagate."""
    new_allow = list(dict.fromkeys(sb.get_egress() + [host]))
    sink.emit("egress", f"+ {host} added to the allowlist", allow=new_allow, added=[host], reason=reason)
    if hasattr(sb, "allow_egress"):
        sb.allow_egress([host], wait=wait)
    else:
        sb.set_egress(new_allow)


def _controls(desktop: Any) -> str:
    if not hasattr(desktop, "eval_js"):
        return ""
    try:
        items = desktop.eval_js(CONTROLS_JS) or []
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(f"[{c['i']}] {c['tag']} '{c['text']}' at ({c['x']}, {c['y']})" for c in items)


def _slug(url: str, task: str) -> str:
    words = re.findall(r"[a-z0-9]+", f"{_host(url).replace('www.', '')} {urlparse(url).path}".lower())
    return "-".join(words)[:40].strip("-") or "unclick-api"


class _Throttle:
    def __init__(self, every: float):
        self.every, self.last = every, 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            return True
        return False


def _retire_previous_sandboxes(sink: EventSink, wait_s: int = 120) -> None:
    """Free plan = 1 concurrent sandbox: retire the previously published API so a new run can start."""
    import httpx

    key = (os.getenv("CREATEOS_PROJECT_API_KEY") or os.getenv("CREATEOS_SANDBOX_API_KEY")
           or os.getenv("CREATEOS_API_KEY"))
    base = os.getenv("CREATEOS_SANDBOX_BASE_URL") or "https://api-eu.sb.createos.sh"
    h = {"X-Api-Key": key}
    sink.emit("log", "CreateOS plan allows 1 sandbox at a time: retiring the previous API sandbox", level="warn")
    data = httpx.get(f"{base}/v1/sandboxes", headers=h, timeout=30).json().get("data")
    items = data.get("data", []) if isinstance(data, dict) else (data or [])
    for s in items:
        if s.get("status") not in ("destroyed", "destroying"):
            httpx.delete(f"{base}/v1/sandboxes/{s['id']}", headers=h, timeout=30)
            sink.emit("sandbox", "Retired previous sandbox", id=s["id"], role="api", action="destroyed")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        st = httpx.get(f"{base}/v1/whoami", headers=h, timeout=30).json()["data"]["stats"]
        if st.get("running", 0) + st.get("paused", 0) + st.get("other", 0) == 0:
            return
        time.sleep(4)


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

def run(url: str, task: str, sink: EventSink, factory: SandboxFactory | None = None,
        desktop_factory: Callable[[Sandbox], Desktop] | None = None, agent: Any = None) -> RunResult:
    t_start = time.monotonic()
    desktop_sb: Sandbox | None = None
    runner: Sandbox | None = None
    published = False
    phase = "boot"
    endpoint_url: str | None = None
    code: str | None = None
    attempts = 0
    sample: Any = None

    def destroy(sb: Sandbox | None, role: str) -> None:
        if sb is None:
            return
        try:
            t0 = time.perf_counter()
            sb.destroy()
            sink.emit("sandbox", f"Destroyed {role} sandbox", id=sb.id, role=role, action="destroyed",
                      ms=round((time.perf_counter() - t0) * 1000, 1))
        except Exception as e:  # noqa: BLE001
            sink.emit("log", f"Could not destroy {role} sandbox {sb.id}: {e}", level="warn")

    try:
        if agent is None:
            from unclick.agent import Agent
            agent = Agent()
        if factory is None:
            from unclick.sandbox import default_factory
            factory = default_factory()
        sink.emit("log", f"Agent model: {agent.model}", level="info")

        # ---- boot ----------------------------------------------------------
        sink.emit("status", "Booting a desktop sandbox with Chrome", phase="boot")
        egress = _site_egress(url)
        if desktop_factory is None:
            # unclick/desktop.py boots desktop:1, starts Chrome + recorder, emits sandbox + live_view.
            from unclick.desktop import create_desktop
            from unclick.sandbox import PlanLimitError
            try:
                desktop = create_desktop(factory, egress, sink)
            except PlanLimitError as e:
                if "concurrent" not in str(e):
                    raise
                _retire_previous_sandboxes(sink)
                desktop = create_desktop(factory, egress, sink)
            desktop_sb = desktop.sandbox
        else:
            t0 = time.perf_counter()
            desktop_sb = factory.create(rootfs="desktop:1", shape=DESKTOP_SHAPE, egress=egress, ingress=True,
                                        name=f"unclick-desk-{int(time.time())}")
            sink.emit("sandbox", "Desktop sandbox is running", id=desktop_sb.id, role="desktop",
                      action="created", ms=round((time.perf_counter() - t0) * 1000, 1))
            desktop = desktop_factory(desktop_sb)
            try:
                sink.emit("live_view", "Live view of the desktop", url=desktop.live_view_url())
            except Exception as e:  # noqa: BLE001
                sink.emit("log", f"Live view unavailable: {e}", level="warn")
        sink.emit("egress", f"Egress locked to {_host(url)}", allow=egress,
                  reason=f"Chrome may only reach the target site ({_host(url)}); everything else is denied.")

        # ---- explore -------------------------------------------------------
        phase = "explore"
        sink.emit("status", "Agent is doing the task in Chrome", phase="explore")
        shot_throttle = _Throttle(SCREENSHOT_EVERY_S)

        def screenshot(force: bool = False) -> bytes | None:
            try:
                png = desktop.screenshot()
            except Exception as e:  # noqa: BLE001
                sink.emit("log", f"Screenshot failed: {e}", level="warn")
                return None
            if force or shot_throttle.ready():
                import base64
                sink.emit("screenshot", "Screen", png_b64=base64.b64encode(png).decode())
            return png

        def page_text() -> str:
            try:
                return desktop.page_text() or ""
            except Exception as e:  # noqa: BLE001
                sink.emit("log", f"page_text failed: {e}", level="warn")
                return ""

        desktop.open(url)
        sink.emit("browser_action", f"Open {url}", action="open", detail=url)
        time.sleep(OPEN_WAIT_S)
        state = agent.explore_start(url, task)
        text = ""
        rejections = 0
        allowed_live: set[str] = set()
        t_explore = time.monotonic()
        for step in range(1, MAX_EXPLORE_STEPS + 1):
            if time.monotonic() - t_explore > EXPLORE_BUDGET_S:
                sink.emit("log", "Explore time budget reached; moving on", level="info")
                break
            png = screenshot()
            text = page_text()
            ptoks = _tokens(text)
            json_caps = [c for c in desktop.captured_requests(json_only=False) if _is_jsonish(c)]
            hits = [c for c in json_caps if _relevant(c, ptoks)]
            caps = [_cap_line(c) + ("  <-- contains on-screen data" if c in hits else "") for c in json_caps[-15:]]
            if not hits:
                # The page tried an API host that egress blocks (e.g. a search backend): allow exactly that host.
                for bh in _blocked_hosts(desktop, desktop_sb, url)[:1]:
                    if bh not in allowed_live:
                        allowed_live.add(bh)
                        _allow_host(desktop_sb, bh, sink, f"The page tried to fetch data from {bh}, which the "
                                    "allowlist blocked. Allowing exactly that host.")
                        caps.append(f"EGRESS: the page tried {bh} and was blocked; it is allowed now. Repeat the "
                                    "action that loads the data (or reload) so the request succeeds.")
            caps.append("DATA CHECK: " + (f"{len(hits)} captured JSON response(s) contain the on-screen data."
                                          if hits else "no captured JSON response contains the on-screen data yet."))
            call = agent.explore_next(state, url=url, task=task, step=step, max_steps=MAX_EXPLORE_STEPS,
                                      screenshot=png, page_text=text, captures=caps, controls=_controls(desktop))
            a = call.args
            if call.name == "done":
                if not hits and rejections < MAX_DONE_REJECTIONS and step < MAX_EXPLORE_STEPS - 1:
                    rejections += 1
                    sink.emit("log", "No captured JSON holds the on-screen data yet; the page is probably "
                                     "server-rendered. Asking the agent to make it re-fetch.", level="info")
                    agent.explore_result(state, call, prompts.NOT_DONE)
                    continue
                changed = _changed_filters(desktop) if hits else []
                if changed and rejections < MAX_DONE_REJECTIONS and step < MAX_EXPLORE_STEPS - 1:
                    rejections += 1
                    sink.emit("log", "Data captured; asking the agent to restore the default view", level="info")
                    agent.explore_result(state, call, "Not done yet: the data was captured, but the page still shows a "
                                         "filtered view. Restore the default view first: " + "; ".join(changed))
                    continue
                sink.emit("log", f"Explore done: {a.get('summary', '')}", level="info")
                break
            try:
                if call.name == "open":
                    desktop.open(a["url"])
                    detail = a["url"]
                elif call.name == "click":
                    desktop.click(int(a["x"]), int(a["y"]))
                    detail = f"({a['x']}, {a['y']}) {a.get('label', '')}".strip()
                elif call.name == "type":
                    desktop.type(str(a["text"]))
                    detail = str(a["text"])
                elif call.name == "press":
                    keys = a["keys"] if isinstance(a.get("keys"), list) else [str(a.get("keys"))]
                    desktop.press(keys)
                    detail = "+".join(keys)
                elif call.name == "select_option":
                    if not hasattr(desktop, "eval_js"):
                        raise ValueError("select_option is not available here; click the dropdown and use keys")
                    res = desktop.eval_js(f"{SELECT_JS}({int(a['control'])}, {json.dumps(str(a['option']))})")
                    if str(res).startswith("error"):
                        raise ValueError(res)
                    detail = f"dropdown #{a['control']}: {str(res).removeprefix('selected ')}"
                    sink.emit("browser_action", f"select {detail}", action="click", detail=detail)
                    agent.explore_result(state, call, str(res))
                    time.sleep(ACTION_WAIT_S)
                    continue
                elif call.name == "scroll":
                    desktop.scroll(int(a["dy"]))
                    detail = f"dy={a['dy']}"
                else:
                    raise ValueError(f"unknown tool {call.name}")
                sink.emit("browser_action", f"{call.name} {detail}", action=call.name, detail=detail)
                agent.explore_result(state, call, "ok")
            except Exception as e:  # noqa: BLE001
                sink.emit("log", f"Action {call.name} failed: {e}", level="warn")
                agent.explore_result(state, call, f"error: {e}")
            time.sleep(ACTION_WAIT_S if call.name != "open" else OPEN_WAIT_S)
        screenshot(force=True)
        text = page_text() or text

        # ---- discover ------------------------------------------------------
        phase = "discover"
        sink.emit("status", "Looking for the hidden API behind the page", phase="discover")
        page_tokens = _tokens(text)
        task_tokens = _tokens(task)
        cands = [c for c in desktop.captured_requests(json_only=False) if _is_jsonish(c)]
        seen: dict[str, CapturedRequest] = {}
        for c in cands:  # keep the latest capture per method+url
            seen[f"{c.method} {c.url}"] = c
        ranked = sorted(seen.values(), key=lambda c: _score(c, page_tokens, task_tokens), reverse=True)
        if not any(_overlap(c, page_tokens) >= MIN_OVERLAP for c in ranked):
            for c in ranked[:3]:
                sink.emit("capture", f"{c.method} {c.url}", url=c.url, method=c.method, status=c.status,
                          content_type=c.content_type, size=len(c.response_body or ""))
            raise RuntimeError(f"None of the {len(ranked)} captured JSON responses contain the data on screen; "
                               "the page is server-rendered and no interaction made it re-fetch.")
        ranked = [c for c in ranked if _overlap(c, page_tokens) >= MIN_OVERLAP]
        top = ranked[:5]
        for c in top:
            sink.emit("capture", f"{c.method} {c.url}", url=c.url, method=c.method, status=c.status,
                      content_type=c.content_type, size=len(c.response_body or ""))
        summaries = []
        for i, c in enumerate(top):
            summaries.append(f"[{i}] {_cap_line(c)} heuristic={_score(c, page_tokens, task_tokens)}\n"
                             f"    body: {(c.response_body or '')[:600]}")
        idx, why = (0, "Only candidate.") if len(top) == 1 else agent.pick_endpoint(task, text, summaries)
        ep = top[idx]
        endpoint_url = ep.url
        sink.emit("endpoint", f"Hidden API: {ep.method} {ep.url}", url=ep.url, method=ep.method, why=why)

        ep_host = _host(ep.url)
        if not _egress_allows(desktop_sb.get_egress(), ep_host):
            _allow_host(desktop_sb, ep_host, sink, f"The data comes from a different host ({ep_host}); "
                        "allowing exactly that host.", wait=False)

        expected = agent.extract_expected(task, text)
        sink.emit("log", f"Ground truth from the screen: {json.dumps(expected, ensure_ascii=False)[:600]}",
                  level="info")

        destroy(desktop_sb, "desktop")
        desktop_sb = None

        # ---- code + verify -------------------------------------------------
        phase = "code"
        sink.emit("status", "Writing a Python client for the hidden API", phase="code")
        runner_egress = [ep_host]
        t0 = time.perf_counter()
        runner = _create_with_slot_wait(factory, sink, rootfs="devbox:1", shape=RUNNER_SHAPE, egress=runner_egress,
                                        ingress=False, name=f"unclick-run-{int(time.time())}")
        sink.emit("sandbox", "Runner sandbox is running", id=runner.id, role="runner", action="created",
                  ms=round((time.perf_counter() - t0) * 1000, 1))
        sink.emit("egress", f"Runner egress locked to {ep_host}", allow=runner_egress,
                  reason="The generated client may only talk to the hidden API host.")
        _ensure_requests(runner, runner_egress, sink)

        body_json = _try_json(ep.response_body or "")
        headers = {k: v for k, v in (ep.request_headers or {}).items() if not DROP_HEADERS.match(k)}
        ctx = dict(
            task=task, page_url=url, method=ep.method, url=ep.url, status=ep.status, content_type=ep.content_type,
            request_headers=json.dumps(headers, indent=1)[:1500], request_body=(ep.request_body or "")[:1500] or "none",
            structure="\n".join(_structure(body_json))[:2500] if body_json is not None else "(not JSON)",
            response_sample=json.dumps(_trim(body_json), ensure_ascii=False)[:3500] if body_json is not None
            else (ep.response_body or "")[:3500],
            expected=json.dumps(expected, ensure_ascii=False),
            endpoint_params=json.dumps(dict(parse_qsl(urlparse(ep.url).query)), ensure_ascii=False)[:800] or "{}",
            page_params=json.dumps(dict(parse_qsl(urlparse(url).query)), ensure_ascii=False)[:500] or "{}")

        query_inputs = [v for u in (url, ep.url) for _, v in parse_qsl(urlparse(u).query)]
        for k, v in parse_qsl(ep.request_body or "") if not (ep.request_body or "").lstrip().startswith("{") else ():
            query_inputs.append(v)
        body_req = _try_json(ep.request_body or "")
        if isinstance(body_req, dict):
            query_inputs += [str(v) for v in body_req.values() if isinstance(v, (str, int, float))]
            query_inputs += [v for _, v in parse_qsl(str(body_req.get("params", "")))]
        conv: Any = None
        feedback: dict[str, Any] = {}
        ok = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts = attempt
            if attempt == 1:
                code, conv = agent.write_client(ctx)
            else:
                code = agent.fix_client(conv, feedback)
            sink.emit("code", f"client.py (attempt {attempt})", attempt=attempt, filename="client.py", code=code)
            runner.write_file(CLIENT_PATH, code)
            phase = "verify"
            sink.emit("status", f"Running client.py (attempt {attempt})", phase="verify")
            res = runner.exec(f"cd {WORKDIR} && python3 client.py", timeout=60)
            sink.emit("run", f"python3 client.py -> exit {res.exit_code}", attempt=attempt, exit_code=res.exit_code,
                      stdout=res.stdout[:4000], stderr=res.stderr[-3000:], ms=round(res.ms, 1))
            hard = hardcoded_values(code, expected, query_inputs)
            if res.exit_code != 0:
                ok, got, notes = False, res.stdout[:800], "The script crashed; see stderr."
            elif hard:
                ok, got, notes = False, res.stdout[:800], (f"client.py hard-codes on-screen values {hard}; every value "
                                                           "must come from the HTTP response.")
            else:
                ok, got, notes = verify_output(res.stdout, expected)
            sink.emit("verify", ("Output matches the screen" if ok else "Output does not match yet")
                      + f" (attempt {attempt})", attempt=attempt, ok=ok,
                      expected=json.dumps(expected, ensure_ascii=False)[:1500], got=got, notes=notes)
            if ok:
                sample = _try_json(res.stdout)
                break
            feedback = dict(attempt=attempt, exit_code=res.exit_code, stderr=res.stderr[-2500:] or "(empty)",
                            stdout=res.stdout[:1500] or "(empty)", notes=notes)
        if not ok:
            raise RuntimeError(f"client.py did not verify after {attempts} attempts")
        _param_demo(runner, sample, attempts, sink)

        # ---- publish -------------------------------------------------------
        phase = "publish"
        sink.emit("status", "Publishing the client as a paid API", phase="publish")
        api = None
        try:
            from unclick.publish import publish
        except Exception as e:  # noqa: BLE001  (missing module or a broken import must not lose a verified client)
            sink.emit("log", f"Publish skipped: unclick.publish not importable ({type(e).__name__}: {e})", level="warn")
        else:
            import inspect
            params = inspect.signature(publish).parameters
            extra = {k: v for k, v in (("source_site", url), ("egress_hosts", [ep_host]), ("task", task),
                                       ("sample_output", sample)) if k in params}
            api = publish(runner, code, sink, name=_slug(url, task), **extra)
            published = True
            if api is not None and getattr(api, "sandbox_id", None) == runner.id:
                runner = None  # the runner itself serves the API: keep it alive
        if api is not None:
            sink.emit("status", "API is live", phase="live")
        secs = time.monotonic() - t_start
        summary = (f"Unclicked {_host(url)} into {ep.method} {endpoint_url} in {attempts} attempt(s), {secs:.0f}s"
                   + (f"; live at {api.api_url}" if api else ""))
        sink.emit("done", summary, summary=summary, api_url=api.api_url if api else None)
        return RunResult(ok=True, endpoint=endpoint_url, client_code=code, sample_output=sample,
                         attempts=attempts, api=api)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        sink.emit("error", err, where=phase, traceback=traceback.format_exc()[-3000:])
        sink.emit("status", "Run failed", phase="failed")
        sink.emit("done", f"Run failed during {phase}", summary=err)
        return RunResult(ok=False, endpoint=endpoint_url, client_code=code, sample_output=sample,
                         attempts=attempts, error=err)
    finally:
        destroy(desktop_sb, "desktop")
        if runner is not None and not published:
            destroy(runner, "runner")


PARAM_DEMO = f"""import json, sys
sys.path.insert(0, {WORKDIR!r})
import client
p = dict(getattr(client, "EXAMPLE_PARAMS", None) or {{}})
print(json.dumps({{"params": p, "data": client.run(**p)}}, ensure_ascii=False))
"""


def _param_demo(runner: Sandbox, sample: Any, attempt: int, sink: EventSink) -> None:
    """Call run(**EXAMPLE_PARAMS) once to show the API is parameterized (not a fixed scrape). Never fatal."""
    try:
        runner.write_file(f"{WORKDIR}/param_demo.py", PARAM_DEMO)
        res = runner.exec(f"cd {WORKDIR} && python3 param_demo.py", timeout=60)
        out = _try_json(res.stdout) if res.exit_code == 0 else None
        params = out.get("params", {}) if isinstance(out, dict) else {}
        data = out.get("data") if isinstance(out, dict) else None
        changed = data is not None and data != sample
        args = ", ".join(f"{k}={v!r}" for k, v in params.items()) or "defaults"
        sink.emit("run", f"client.run({args}) -> exit {res.exit_code}" + (", different data" if changed else ""),
                  attempt=attempt, exit_code=res.exit_code, filename="client.py", params=params, changed=changed,
                  stdout=json.dumps(data, indent=2, ensure_ascii=False)[:4000] if data is not None else res.stdout[:4000],
                  stderr=res.stderr[-3000:], ms=round(res.ms, 1))
        if res.exit_code == 0 and not changed:
            sink.emit("log", f"run({args}) returned the same data as the defaults", level="warn")
    except Exception as e:  # noqa: BLE001
        sink.emit("log", f"Parameter demo failed: {type(e).__name__}: {e}", level="warn")


def _create_with_slot_wait(factory: SandboxFactory, sink: EventSink, **kw: Any) -> Sandbox:
    """Create a sandbox, waiting out plan-cap 429s (a just-destroyed sandbox frees its slot a few seconds later)."""
    deadline = time.monotonic() + SLOT_WAIT_S
    told = False
    while True:
        try:
            return factory.create(**kw)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            capped = "concurrent" in msg and "per-day" not in msg  # a daily cap will not clear by waiting
            if not capped or time.monotonic() > deadline:
                raise
            if not told:
                sink.emit("log", "Sandbox plan slot busy; waiting for it to free up", level="info")
                told = True
            time.sleep(3)


def _ensure_requests(sb: Sandbox, egress: list[str], sink: EventSink) -> None:
    """devbox:1 usually ships `requests`; if not, open PyPI briefly, install, and lock egress again."""
    if sb.exec('python3 -c "import requests"', timeout=30).exit_code == 0:
        return
    sink.emit("log", "Installing requests (PyPI opened briefly)", level="info")
    sb.set_egress(egress + PIP_EGRESS)
    try:
        r = sb.exec("python3 -m pip install -q requests || python3 -m pip install -q --break-system-packages requests",
                    timeout=120)
        if r.exit_code != 0:
            sink.emit("log", f"pip install requests failed: {r.stderr[-300:]}", level="warn")
    finally:
        sb.set_egress(egress)
