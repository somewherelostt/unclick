"""Live smoke test for unclick/desktop.py: desktop:1 + Chrome CDP + recorder on projects.dev.

    python scripts/smoke_desktop.py [url] [--keep 60]   # --keep: seconds to leave it up for watching
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unclick.contracts import PrintSink  # noqa: E402
from unclick.desktop import create_desktop  # noqa: E402
from unclick.sandbox import SandboxError, default_factory  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:]]
    keep = 0
    if "--keep" in args:
        i = args.index("--keep")
        keep = int(args[i + 1])
        del args[i:i + 2]
    url = args[0] if args else "https://projects.dev/leaderboard"
    egress = os.getenv("UNCLICK_DESKTOP_EGRESS")
    egress_list = egress.split(",") if egress else None  # default: open egress
    sink = PrintSink()
    d = None
    ok = True
    try:
        t0 = time.perf_counter()
        d = create_desktop(default_factory(), egress_list, sink)
        print(f"\nsetup took {time.perf_counter() - t0:.1f}s")
        print("probe:", d.probe)
        print("LIVE VIEW:", d.live_view_url())

        d.open(url, settle=4)
        print("current url:", d.current_url())
        text = d.page_text()
        print(f"\npage_text ({len(text)} chars):\n{text[:800]}\n")

        if "projects.dev/leaderboard" in url:
            # The leaderboard is server-rendered; its JSON API only fires when the hackathon filter changes.
            for label in ("CreateOS 2026", "All Projects"):
                seq = d.last_seq()
                print(f"select -> {label!r}:", d.select_option("select", label))
                hits = d.wait_for_capture("/api/leaderboard", since_seq=seq, timeout=15)
                for c in hits:
                    print(f"  captured #{c.seq} {c.method} {c.status} {c.url} body[:200]={c.response_body[:200]!r}")
                if not hits:
                    print("  (no /api/leaderboard capture)")

        d.scroll(600)
        time.sleep(2)
        png = d.screenshot()
        png_path = os.path.join(tempfile.gettempdir(), "smoke_desktop.png")
        open(png_path, "wb").write(png)
        print("screenshot bytes:", len(png), "->", png_path)

        caps = d.captured_requests()
        print(f"\ncaptured JSON requests: {len(caps)}")
        for c in caps:
            print(f"  #{c.seq} {c.method} {c.status} {c.content_type[:40]:40} {len(c.response_body):>7}B {c.url[:140]}")
        allcaps = d.captured_requests(json_only=False)
        print(f"all XHR/fetch captures: {len(allcaps)}")
        for c in allcaps:
            if c not in caps:
                print(f"  #{c.seq} {c.method} {c.status} {c.content_type[:40]:40} {len(c.response_body):>7}B {c.url[:140]}")
        print("\nrecorder log tail:\n", d.recorder_log(1500))
        if keep:
            print(f"keeping desktop up for {keep}s; live view: {d.live_view_url()}")
            time.sleep(keep)
    except SandboxError as e:
        ok = False
        print("FAILED:", e)
        if d is not None:
            try:
                print("recorder log:", d.recorder_log(1500))
                print("chrome log:", d.sandbox.exec("tail -c 1500 /tmp/unclick/chrome.log").stdout)
            except Exception:  # noqa: BLE001
                pass
    finally:
        if d is not None:
            d.sandbox.destroy()
            sink.emit("sandbox", f"destroyed {d.sandbox.id}", id=d.sandbox.id, role="desktop", action="destroyed")
    print("\nRESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
