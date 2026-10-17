"""Live smoke test for unclick/sandbox.py against the real CreateOS API.

    python scripts/smoke_sandbox.py            # full run (fork needs 2 concurrent slots)
    python scripts/smoke_sandbox.py --no-fork  # Free plan (1 slot)
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from unclick.sandbox import PlanLimitError, SandboxError, default_factory  # noqa: E402


def step(name: str) -> None:
    print(f"\n=== {name}", flush=True)


def main() -> int:
    do_fork = "--no-fork" not in sys.argv
    f = default_factory()
    step("whoami")
    print(f.whoami())
    sb = child = None
    ok = True
    try:
        step("create devbox:1")
        t0 = time.perf_counter()
        sb = f.create(rootfs="devbox:1", shape="s-1vcpu-1gb", ingress=False)
        print("id", sb.id, f"{(time.perf_counter() - t0) * 1000:.0f} ms", "status", sb.status())

        step("exec")
        r = sb.exec("python3 -c 'print(6*7)'; echo err >&2")
        print(r)
        assert r.stdout.strip() == "42" and r.exit_code == 0

        step("write/read file")
        sb.write_file("/tmp/unclick/hello.txt", "hello unclick\n")
        assert sb.read_file("/tmp/unclick/hello.txt") == b"hello unclick\n"
        print(sb.exec("cat /tmp/unclick/hello.txt").stdout.strip())

        step("egress lockdown")
        sb.set_egress(["example.com"])
        print("egress now", sb.get_egress())
        time.sleep(2)
        blocked = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://pypi.org/simple/ || echo BLOCKED", timeout=30)
        print("pypi.org (should be blocked):", blocked.stdout.strip(), blocked.exit_code)
        allowed = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://example.com/", timeout=30)
        print("example.com (allowed):", allowed.stdout.strip())
        sb.set_egress(["example.com", "pypi.org"])
        time.sleep(3)
        now = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://pypi.org/simple/ || echo BLOCKED", timeout=30)
        print("pypi.org after allow:", now.stdout.strip())
        sb.set_egress([])
        print("egress reopened", sb.get_egress())

        step("ingress + preview_url")
        sb.enable_ingress()
        sb.write_file("/tmp/www/index.html", "<h1>unclick ingress ok</h1>")
        pid = sb.start_process("cd /tmp/www && python3 -m http.server 8000 --bind 0.0.0.0")
        print("process", pid)
        print("port ready:", sb.wait_for_port(8000, timeout=20))
        url = sb.preview_url(8000)
        print("preview_url", url, "| template:", sb.info.get("ingress_url_template"))
        for scheme_url in (url, url.replace("https://", "http://", 1)):
            for i in range(10):
                try:
                    resp = httpx.get(scheme_url + "/", timeout=10, follow_redirects=True)
                    print(scheme_url, "->", resp.status_code, resp.text[:60].strip())
                    if resp.status_code == 200:
                        break
                except httpx.HTTPError as e:
                    print(scheme_url, "-> error", repr(e)[:120])
                time.sleep(2)

        step("pause")
        t0 = time.perf_counter()
        sb.pause()
        print("paused in", f"{(time.perf_counter() - t0) * 1000:.0f} ms", sb.status())

        if do_fork:
            step("fork")
            t0 = time.perf_counter()
            try:
                child = sb.fork(egress=["example.com"], ingress=True)
                print("child", child.id, f"{(time.perf_counter() - t0) * 1000:.0f} ms")
                print("child sees file:", child.exec("cat /tmp/unclick/hello.txt").stdout.strip())
                print("child egress:", child.get_egress())
                child.destroy()
                child = None
            except PlanLimitError as e:
                print("FORK HIT PLAN LIMIT (needs >=2 concurrent sandboxes):", e)

        step("resume")
        t0 = time.perf_counter()
        sb.resume()
        print("resumed in", f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        print("after resume:", sb.exec("cat /tmp/unclick/hello.txt").stdout.strip())

        step("auto-pause setting")
        sb.set_auto_pause(60)
        print("auto_pause_after_seconds:", sb.refresh().get("auto_pause_after_seconds"))
    except SandboxError as e:
        ok = False
        print("FAILED:", e)
    finally:
        step("destroy")
        for s in (child, sb):
            if s is not None:
                try:
                    s.destroy()
                    print("destroyed", s.id)
                except Exception as e:  # noqa: BLE001
                    print("destroy failed", s.id, e)
    print("\nRESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
