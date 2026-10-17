"""Network recorder. Runs inside the desktop sandbox next to Chrome (--remote-debugging-port).

Attaches to every page target over CDP and appends one JSON line per XHR/fetch (or any JSON)
response to --out. Bodies are truncated to 200 KB. Serves nothing.

    python3 recorder.py --port 9222 --out /tmp/unclick/captures.jsonl
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdpws import CDP, http_json  # noqa: E402

MAX_BODY = 200 * 1024
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".ico", ".webp", ".mp4")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def lower_keys(h: dict | None) -> dict:
    return {str(k).lower(): str(v) for k, v in (h or {}).items()}


def interesting(rtype: str, mime: str, url: str) -> bool:
    mime = (mime or "").lower()
    if "json" in mime:
        return True
    if rtype in ("XHR", "Fetch"):
        path = url.split("?", 1)[0].lower()
        return not path.endswith(SKIP_EXT) and not mime.startswith(("image/", "font/", "video/", "audio/"))
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--out", default="/tmp/unclick/captures.jsonl")
    ap.add_argument("--ready", default="/tmp/unclick/recorder.ready")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    for _ in range(120):
        try:
            ws_url = http_json(args.port, "/json/version")["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    else:
        log("CDP not reachable on port", args.port)
        sys.exit(2)

    cdp = CDP(ws_url)
    cdp.ws.sock.settimeout(None)
    out = open(args.out, "a", encoding="utf-8")
    seq = 0
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            seq = sum(1 for _ in f)

    sessions: set[str] = set()
    reqs: dict[tuple, dict] = {}       # (session, requestId) -> info
    pending: dict[int, tuple] = {}     # cmd id -> (session, requestId)

    def attach(target_id: str):
        cdp.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})

    def on_attached(sid: str):
        if sid in sessions:
            return
        sessions.add(sid)
        cdp.send("Network.enable", {"maxTotalBufferSize": 50_000_000, "maxResourceBufferSize": 10_000_000}, sid)
        cdp.send("Page.enable", {}, sid)
        cdp.send("Runtime.runIfWaitingForDebugger", {}, sid)
        log("attached", sid)

    cdp.call("Target.setDiscoverTargets", {"discover": True})
    for t in cdp.call("Target.getTargets").get("targetInfos", []):
        if t.get("type") == "page":
            attach(t["targetId"])
    # drain attach responses briefly, then signal ready
    t_end = time.time() + 2
    while time.time() < t_end:
        if not cdp.ws.readable(0.3):
            continue
        msg = cdp.recv()
        if msg.get("method") == "Target.attachedToTarget":
            on_attached(msg["params"]["sessionId"])
        elif "sessionId" in msg.get("result", {}):
            on_attached(msg["result"]["sessionId"])
    with open(args.ready, "w") as f:
        f.write(str(os.getpid()))
    log("ready; sessions:", len(sessions))

    def write(info: dict, body: str, b64: bool):
        nonlocal seq
        if b64:
            try:
                body = base64.b64decode(body).decode("utf-8")
            except Exception:
                body = "<binary>"
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY]
        seq += 1
        rec = {
            "seq": seq,
            "ts": time.time(),
            "url": info.get("url", ""),
            "method": info.get("method", "GET"),
            "status": int(info.get("status", 0) or 0),
            "content_type": info.get("content_type", ""),
            "resource_type": info.get("type", ""),
            "request_headers": info.get("request_headers", {}),
            "request_body": info.get("post_data"),
            "response_headers": info.get("response_headers", {}),
            "response_body": body,
        }
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        log("captured", seq, rec["method"], rec["status"], rec["url"][:120])

    while True:
        try:
            msg = cdp.recv()
        except Exception as e:  # Chrome gone
            log("CDP connection lost:", repr(e))
            sys.exit(1)

        mid = msg.get("id")
        if mid is not None:
            res = msg.get("result", {})
            if "sessionId" in res:
                on_attached(res["sessionId"])
            key = pending.pop(mid, None)
            if key:
                info = reqs.pop(key, None)
                if info is not None:
                    if "error" in msg:
                        write(info, "", False)
                    else:
                        write(info, res.get("body", ""), res.get("base64Encoded", False))
            continue

        method = msg.get("method", "")
        p = msg.get("params", {})
        sid = msg.get("sessionId")

        if method == "Target.targetCreated":
            ti = p.get("targetInfo", {})
            if ti.get("type") == "page" and not ti.get("attached"):
                attach(ti["targetId"])
        elif method == "Target.attachedToTarget":
            on_attached(p["sessionId"])
        elif method == "Target.detachedFromTarget":
            sessions.discard(p.get("sessionId"))
        elif method == "Network.requestWillBeSent":
            r = p.get("request", {})
            key = (sid, p["requestId"])
            prev = reqs.get(key, {})
            reqs[key] = {
                **prev,
                "url": r.get("url", ""),
                "method": r.get("method", "GET"),
                "request_headers": {**r.get("headers", {}), **prev.get("request_headers", {})},
                "post_data": r.get("postData"),
                "type": p.get("type", ""),
            }
        elif method == "Network.requestWillBeSentExtraInfo":
            key = (sid, p["requestId"])
            info = reqs.setdefault(key, {})
            info["request_headers"] = {**info.get("request_headers", {}), **p.get("headers", {})}
        elif method == "Network.responseReceived":
            key = (sid, p["requestId"])
            r = p.get("response", {})
            info = reqs.setdefault(key, {})
            hdrs = lower_keys(r.get("headers"))
            info.update({
                "url": r.get("url", info.get("url", "")),
                "status": r.get("status", 0),
                "response_headers": hdrs,
                "content_type": hdrs.get("content-type", r.get("mimeType", "")),
                "type": p.get("type", info.get("type", "")),
            })
            if not interesting(info["type"], info["content_type"], info["url"]):
                info["skip"] = True
        elif method == "Network.loadingFinished":
            key = (sid, p["requestId"])
            info = reqs.get(key)
            if info is None or info.get("skip") or "status" not in info:
                reqs.pop(key, None)
                continue
            if info.get("method") == "OPTIONS":
                reqs.pop(key, None)
                continue
            cid = cdp.send("Network.getResponseBody", {"requestId": p["requestId"]}, sid)
            pending[cid] = key
        elif method == "Network.loadingFailed":
            reqs.pop((sid, p.get("requestId")), None)

        if len(reqs) > 5000:  # leak guard
            for k in list(reqs)[:2500]:
                reqs.pop(k, None)


if __name__ == "__main__":
    main()
