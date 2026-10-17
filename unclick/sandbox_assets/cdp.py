"""One-shot CDP helper run inside the desktop sandbox. Prints a JSON result on stdout.

    python3 cdp.py eval 'document.body.innerText'
    python3 cdp.py navigate https://projects.dev/leaderboard
    python3 cdp.py info
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdpws import CDP, page_ws_url  # noqa: E402

PORT = int(os.environ.get("CDP_PORT", "9222"))


def evaluate(cdp: CDP, expr: str):
    res = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    if "exceptionDetails" in res:
        raise RuntimeError(res["exceptionDetails"].get("text", "js exception") + " " +
                           str(res["exceptionDetails"].get("exception", {}).get("description", ""))[:500])
    return res.get("result", {}).get("value")


def main() -> None:
    cmd, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    cdp = CDP(page_ws_url(PORT))
    try:
        if cmd == "eval":
            out = {"value": evaluate(cdp, arg)}
        elif cmd == "navigate":
            cdp.call("Page.bringToFront")
            nav = cdp.call("Page.navigate", {"url": arg})
            deadline = time.time() + float(os.environ.get("NAV_TIMEOUT", "20"))
            state = ""
            while time.time() < deadline:
                time.sleep(0.4)
                try:
                    state = evaluate(cdp, "document.readyState")
                except Exception:
                    continue
                if state == "complete":
                    break
            time.sleep(float(os.environ.get("SETTLE", "1.5")))  # let client-side fetches fire
            out = {"frameId": nav.get("frameId"), "errorText": nav.get("errorText"), "readyState": state,
                   "url": evaluate(cdp, "location.href"), "title": evaluate(cdp, "document.title")}
        elif cmd == "info":
            out = {"url": evaluate(cdp, "location.href"), "title": evaluate(cdp, "document.title"),
                   "readyState": evaluate(cdp, "document.readyState")}
        else:
            raise SystemExit(f"unknown command {cmd}")
        print(json.dumps(out, ensure_ascii=False))
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
