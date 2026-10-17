"""Live smoke test for unclick/publish.py + billing against real CreateOS (and Stripe test mode).

    python scripts/smoke_publish.py            # full run: publish, curl, auto-pause, wake, destroy
    python scripts/smoke_publish.py --keep     # leave the API sandbox running at the end
    python scripts/smoke_publish.py --no-wait  # skip the ~60-90 s auto-pause wait

Publishes a trivial client.py that calls https://httpbin.org/json. Works on the Free plan too
(fork fails there and publish falls back to the source sandbox). Never prints secret keys; the
demo key it prints is the free public demo key.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from unclick import billing  # noqa: E402
from unclick.contracts import PrintSink  # noqa: E402
from unclick.publish import publish, wait_until_paused, wake_and_time  # noqa: E402
from unclick.sandbox import default_factory  # noqa: E402

CLIENT = '''import json
import requests

URL = "https://httpbin.org/json"


def run(limit: int = 3, **params):
    r = requests.get(URL, timeout=20, headers={"Accept": "application/json"})
    r.raise_for_status()
    slides = r.json()["slideshow"]["slides"]
    return [{"title": s["title"], "type": s["type"]} for s in slides][: int(limit)]


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
'''


def step(name: str) -> None:
    print(f"\n=== {name}", flush=True)


def main() -> int:
    keep = "--keep" in sys.argv
    sink = PrintSink()
    sk = os.getenv("STRIPE_SECRET_KEY", "")
    if sk and not sk.startswith(("sk_test_", "rk_test_")):
        print("STRIPE_SECRET_KEY is not a test key; refusing to run")
        return 2
    print("stripe billing:", "on (test mode)" if billing.enabled() else "OFF (no sk_test_ key)")

    f = default_factory()
    src = api_sb = None
    ok = True
    try:
        step("create + verify source sandbox (egress httpbin.org)")
        src = f.create(rootfs="devbox:1", shape="s-1vcpu-1gb", egress=["httpbin.org"])
        src.write_file("/srv/unclick/client.py", CLIENT)
        r = src.exec("cd /srv/unclick && python3 client.py", timeout=60)
        print(r.exit_code, r.stdout[:300], r.stderr[-300:])
        assert r.exit_code == 0, "client.py did not run in the source sandbox"

        step("publish")
        t0 = time.perf_counter()
        api = publish(src, CLIENT, sink, "httpbin slides", source_site="https://httpbin.org/json")
        print(f"published in {time.perf_counter() - t0:.1f}s:", api.api_url)
        print("curl:", api.curl)
        api_sb = f.get(api.sandbox_id)
        if api_sb.id == src.id:
            src = None  # fallback path: same sandbox, destroy only once

        demo_key = api.curl.split("X-Unclick-Key: ")[1].split('"')[0]
        step("call with demo key")
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            for i in range(2):
                t0 = time.perf_counter()
                resp = c.get(api.api_url, params={"limit": 2}, headers={"X-Unclick-Key": demo_key})
                print(resp.status_code, f"{(time.perf_counter() - t0) * 1000:.0f} ms", resp.text[:300])
                assert resp.status_code == 200, resp.text
            bad = c.get(api.api_url, headers={"X-Unclick-Key": "uk_cus_nope_0123456789abcdef0123"})
            print("bad key ->", bad.status_code)
            assert bad.status_code == 401
            print("health:", c.get(api.docs_url.replace("/docs", "/health")).json())
            print("docs page:", c.get(api.docs_url).status_code)

        if billing.enabled():
            step("usage via billing (metadata written back by the sandbox; may lag a few s)")
            time.sleep(5)
            print(billing.usage(demo_key))

        if "--no-wait" not in sys.argv:
            step("wait for auto-pause (60 s idle)")
            print("paused after", wait_until_paused(api_sb, sink, timeout=240), "s")
            step("wake on request")
            ms = wake_and_time(api.api_url, sink, api_sb)
            print(f"wake latency: {ms:.0f} ms")
            print("status after wake:", api_sb.status())
    except Exception as e:  # noqa: BLE001
        ok = False
        print("FAILED:", type(e).__name__, e)
    finally:
        for sb in (src, None if keep else api_sb):
            if sb is not None:
                try:
                    sb.destroy()
                    print("destroyed", sb.id)
                except Exception as e:  # noqa: BLE001
                    print("destroy failed", sb.id, e)
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
