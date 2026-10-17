"""CreateOS Sandbox REST client (sync httpx). Implements contracts.Sandbox / SandboxFactory.

Usage:
    from unclick.sandbox import default_factory
    sb = default_factory().create(rootfs="devbox:1", egress=["pypi.org"], ingress=True)
    try:
        print(sb.exec("python3 -c 'print(1+1)'").stdout)
    finally:
        sb.destroy()
"""

from __future__ import annotations

import os
import random
import shlex
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from unclick.contracts import ExecResult

load_dotenv()

DEFAULT_BASE_URL = "https://api-eu.sb.createos.sh"
RETRY_STATUSES = {429, 502, 503}
TERMINAL_BAD = {"error", "failed", "destroyed", "destroying"}


class SandboxError(RuntimeError):
    """An API call failed. Carries the HTTP status and the API's error body."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None,
                 method: str = "", path: str = ""):
        self.status_code = status_code
        self.body = body
        self.method = method
        self.path = path
        detail = f" [{method} {path} -> {status_code}]" if status_code is not None else ""
        super().__init__(f"{message}{detail}" + (f": {body}" if body not in (None, "") else ""))


class PlanLimitError(SandboxError):
    """429 that survived retries: usually the plan's concurrent-sandbox cap (Free = 1)."""


class SandboxTimeout(SandboxError):
    """A lifecycle wait (create/pause/resume/fork) did not reach the target status in time."""


class _Http:
    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0, retries: int = 4):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Api-Key": api_key, "User-Agent": "unclick/0.1"},
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                content: bytes | None = None, headers: dict | None = None,
                timeout: float | None = None, raw: bool = False,
                retry: bool = True) -> tuple[Any, httpx.Response]:
        """Returns (unwrapped JSend data or raw bytes, response). Raises SandboxError."""
        attempt = 0
        while True:
            try:
                resp = self.client.request(
                    method, path, json=json, params=params, content=content, headers=headers,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.TransportError as e:
                if retry and attempt < self.retries and method in ("GET", "PUT", "DELETE", "PATCH"):
                    attempt += 1
                    time.sleep(min(8.0, 0.5 * 2 ** attempt))
                    continue
                raise SandboxError(f"network error: {e!r}", method=method, path=path) from e

            quota = resp.status_code == 429 and "quota" in resp.text.lower()  # plan cap: retrying won't help
            if resp.status_code in RETRY_STATUSES and retry and attempt < self.retries and not quota:
                attempt += 1
                time.sleep(_retry_delay(resp, attempt))
                continue

            if resp.status_code >= 400:
                body = _error_body(resp)
                cls = PlanLimitError if resp.status_code == 429 else SandboxError
                raise cls("CreateOS API error", resp.status_code, body, method, path)

            if raw:
                return resp.content, resp
            if not resp.content:
                return None, resp
            try:
                payload = resp.json()
            except ValueError:
                return resp.content, resp
            if isinstance(payload, dict) and payload.get("status") in ("success", "fail", "error"):
                if payload["status"] != "success":
                    raise SandboxError("CreateOS API error", resp.status_code, payload, method, path)
                return payload.get("data"), resp
            return payload, resp

    def close(self) -> None:
        self.client.close()


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    ra = resp.headers.get("Retry-After") or resp.headers.get("X-Poll-After")
    if ra:
        try:
            return min(30.0, max(0.2, float(ra)))
        except ValueError:
            pass
    return min(10.0, 0.75 * 2 ** (attempt - 1)) + random.random() * 0.3


def _error_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]


def _poll_after(resp: httpx.Response | None, default: float = 0.5) -> float:
    if resp is not None:
        v = resp.headers.get("X-Poll-After")
        if v:
            try:
                return min(5.0, max(0.2, float(v)))
            except ValueError:
                pass
    return default


class RestSandbox:
    """Handle for one sandbox. Implements contracts.Sandbox."""

    def __init__(self, http: _Http, sandbox_id: str, info: dict | None = None):
        self._http = http
        self.id = sandbox_id
        self.info: dict = info or {}

    def __repr__(self) -> str:
        return f"RestSandbox({self.id!r})"

    # -- internals ---------------------------------------------------------
    def _p(self, suffix: str = "") -> str:
        return f"/v1/sandboxes/{self.id}{suffix}"

    def refresh(self) -> dict:
        data, _ = self._http.request("GET", self._p())
        self.info = data or {}
        return self.info

    def wait_for_status(self, targets: str | set[str], timeout: float = 180.0,
                        first_delay: float = 0.0) -> str:
        targets = {targets} if isinstance(targets, str) else set(targets)
        deadline = time.monotonic() + timeout
        if first_delay:
            time.sleep(first_delay)
        last = ""
        while True:
            data, resp = self._http.request("GET", self._p())
            self.info = data or {}
            last = self.info.get("status", "")
            if last in targets:
                return last
            if last in TERMINAL_BAD and not (targets & TERMINAL_BAD):
                raise SandboxError(f"sandbox {self.id} entered status {last!r} while waiting for {sorted(targets)}",
                                   body=self.info)
            if time.monotonic() > deadline:
                raise SandboxTimeout(f"sandbox {self.id} stuck in {last!r}, wanted {sorted(targets)}")
            time.sleep(_poll_after(resp))

    # -- Sandbox protocol ----------------------------------------------------
    def exec(self, cmd: str, timeout: int = 120, env: dict[str, str] | None = None) -> ExecResult:
        """Run `sh -c cmd`. Exit code 124 means the in-sandbox `timeout` killed it.

        Note: the API only lets `env` override keys declared in `envs` at create time (else 400).
        """
        body: dict[str, Any] = {"cmd": "timeout", "args": ["-k", "5", str(int(timeout)), "sh", "-c", cmd]}
        if env:
            body["env"] = env
        t0 = time.perf_counter()
        data, _ = self._http.request("POST", self._p("/exec"), json=body, timeout=timeout + 30)
        ms = (time.perf_counter() - t0) * 1000
        result = (data or {}).get("result", data or {})
        return ExecResult(
            stdout=result.get("stdout", "") or "",
            stderr=result.get("stderr", "") or "",
            exit_code=int(result.get("exit_code", -1) if result.get("exit_code") is not None else -1),
            ms=float((data or {}).get("exec_ms", ms)),
        )

    def start_process(self, cmd: str, cwd: str | None = None, env: dict[str, str] | None = None) -> str:
        """Managed background process (`sh -c cmd`). Returns the process id (proc_...)."""
        body: dict[str, Any] = {"cmd": "sh", "args": ["-c", cmd]}
        if cwd:
            body["cwd"] = cwd
        if env:
            body["env"] = env
        data, _ = self._http.request("POST", self._p("/processes"), json=body)
        return data["process_id"]

    def process_info(self, process_id: str) -> dict:
        data, _ = self._http.request("GET", self._p(f"/processes/{process_id}"))
        return data or {}

    def process_output(self, process_id: str, after: int | None = None, timeout: float = 3.0) -> str:
        """Best-effort read of a managed process's retained output (NDJSON replay, base64 data)."""
        import base64
        import json as _json
        params = {"after": after} if after is not None else None
        out: list[str] = []
        try:
            with self._http.client.stream("GET", self._p(f"/processes/{process_id}/connect"),
                                          params=params, timeout=timeout) as resp:
                t_end = time.monotonic() + timeout
                for line in resp.iter_lines():
                    if line.strip():
                        try:
                            frame = _json.loads(line)
                        except ValueError:
                            continue
                        d = frame.get("data")
                        if isinstance(d, str):
                            try:
                                out.append(base64.b64decode(d).decode("utf-8", "replace"))
                            except Exception:
                                out.append(d)
                    if time.monotonic() > t_end:
                        break
        except httpx.HTTPError:
            pass
        return "".join(out)

    def kill_process(self, process_id: str) -> None:
        try:
            self._http.request("DELETE", self._p(f"/processes/{process_id}"), retry=False)
        except SandboxError:
            pass

    def write_file(self, path: str, content: str | bytes) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self._http.request("PUT", self._p("/files"), params={"path": path}, content=content,
                           headers={"Content-Type": "application/octet-stream"}, timeout=120)

    def read_file(self, path: str) -> bytes:
        data, _ = self._http.request("GET", self._p("/files"), params={"path": path}, raw=True, timeout=120)
        return data

    def get_egress(self) -> list[str]:
        data, _ = self._http.request("GET", self._p("/egress"))
        return list((data or {}).get("egress") or [])

    def set_egress(self, allow: list[str]) -> None:
        self._http.request("PUT", self._p("/egress"), json={"egress": list(allow)})

    def allow_egress(self, hosts: list[str], wait: bool = True, timeout: int = 60) -> list[str]:
        """Add hosts to a locked allowlist. Live-measured: a newly allowed hostname can take ~30 s
        to become reachable, so with wait=True this polls (curl from inside) until the first host answers."""
        cur = self.get_egress()
        new = cur + [h for h in hosts if h not in cur]
        if cur:  # [] means open; leave it open
            self.set_egress(new)
        if wait and cur and hosts:
            host = hosts[0].split(":")[0].lstrip("*.")
            self.exec(
                f"for i in $(seq 1 {timeout // 2}); do "
                f"c=$(curl -s -o /dev/null -w '%{{http_code}}' --max-time 3 https://{host}/); "
                f"[ \"$c\" != 000 ] && exit 0; sleep 2; done; exit 1", timeout=timeout + 15)
        return new

    def enable_ingress(self) -> None:
        data, _ = self._http.request("PATCH", self._p(), json={"ingress_enabled": True})
        if isinstance(data, dict):
            self.info.update(data)

    def preview_url(self, port: int) -> str:
        tpl = self.info.get("ingress_url_template") or self.refresh().get("ingress_url_template")
        if not tpl:
            raise SandboxError(f"sandbox {self.id} has no ingress_url_template (is ingress enabled?)",
                               body={"ingress_enabled": self.info.get("ingress_enabled")})
        url = tpl.replace("<port>", str(port)).replace("{port}", str(port))
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    def set_auto_pause(self, seconds: int) -> None:
        body = {"disable_auto_pause": True} if seconds <= 0 else {"auto_pause_after_seconds": int(seconds)}
        self._http.request("PATCH", self._p(), json=body)

    def status(self) -> str:
        return self.refresh().get("status", "")

    def pause(self, timeout: float = 180.0) -> None:
        try:
            _, resp = self._http.request("POST", self._p("/pause"))
        except SandboxError as e:
            if e.status_code == 409 and self.status() in ("paused", "pausing"):
                resp = None
            else:
                raise
        self.wait_for_status("paused", timeout, first_delay=_poll_after(resp, 0.3))

    def resume(self, timeout: float = 180.0) -> None:
        try:
            _, resp = self._http.request("POST", self._p("/resume"))
        except SandboxError as e:
            if e.status_code == 409 and self.status() in ("running", "resuming"):
                resp = None
            else:
                raise
        self.wait_for_status("running", timeout, first_delay=_poll_after(resp, 0.3))

    def fork(self, egress: list[str] | None = None, ingress: bool | None = None,
             envs: dict[str, str] | None = None, timeout: float = 240.0) -> "RestSandbox":
        st = self.status()
        if st != "paused":
            raise SandboxError(f"fork requires the source sandbox to be paused (status={st!r}); call pause() first")
        body: dict[str, Any] = {}
        if egress is not None:
            body["egress"] = list(egress)
        if ingress is not None:
            body["ingress_enabled"] = bool(ingress)
        if envs:
            body["envs"] = envs
        data, resp = self._http.request("POST", self._p("/fork"), json=body)
        child = RestSandbox(self._http, data["id"], data)
        try:
            child.wait_for_status("running", timeout, first_delay=_poll_after(resp, 0.5))
        except SandboxError:
            child.destroy()
            raise
        return child

    def destroy(self) -> None:
        try:
            self._http.request("DELETE", self._p())
        except SandboxError as e:
            if e.status_code not in (404, 410):
                raise

    # -- extras ----------------------------------------------------------------
    def wait_for_port(self, port: int, timeout: int = 60) -> bool:
        """Wait until something answers HTTP on 127.0.0.1:port inside the sandbox."""
        r = self.exec(
            f"for i in $(seq 1 {timeout * 2}); do "
            f"curl -s -o /dev/null --max-time 2 http://127.0.0.1:{port}/ && exit 0; sleep 0.5; done; exit 1",
            timeout=timeout + 10,
        )
        return r.exit_code == 0

    def sh(self, cmd: str, timeout: int = 120, check: bool = True) -> str:
        """exec() that raises on non-zero exit and returns stdout."""
        r = self.exec(cmd, timeout=timeout)
        if check and r.exit_code != 0:
            raise SandboxError(f"command failed (exit {r.exit_code}): {cmd[:200]}",
                               body={"stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]})
        return r.stdout

    def __enter__(self) -> "RestSandbox":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.destroy()


class RestSandboxFactory:
    """Implements contracts.SandboxFactory."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        key = api_key or os.getenv("CREATEOS_SANDBOX_API_KEY") or os.getenv("CREATEOS_API_KEY")
        if not key:
            raise SandboxError("CREATEOS_SANDBOX_API_KEY is not set (put it in .env)")
        self.http = _Http(key, base_url or os.getenv("CREATEOS_SANDBOX_BASE_URL") or DEFAULT_BASE_URL)

    def create(self, *, rootfs: str = "devbox:1", shape: str = "s-1vcpu-1gb",
               egress: list[str] | None = None, ingress: bool = False,
               envs: dict[str, str] | None = None, name: str | None = None,
               auto_pause_after_seconds: int | None = None, timeout: float = 240.0) -> RestSandbox:
        body: dict[str, Any] = {"shape": shape, "rootfs": rootfs, "ingress_enabled": bool(ingress)}
        if egress:
            body["egress"] = list(egress)
        if envs:
            body["envs"] = envs
        if name:
            body["name"] = name
        if auto_pause_after_seconds:
            body["auto_pause_after_seconds"] = int(auto_pause_after_seconds)
        data, resp = self.http.request("POST", "/v1/sandboxes", json=body, timeout=timeout)
        sb = RestSandbox(self.http, data["id"], data)
        try:
            if data.get("status") != "running":
                sb.wait_for_status("running", timeout, first_delay=_poll_after(resp, 0.2))
        except SandboxError:
            sb.destroy()
            raise
        return sb

    def get(self, sandbox_id: str) -> RestSandbox:
        sb = RestSandbox(self.http, sandbox_id)
        sb.refresh()
        return sb

    def list(self, status: str | None = None) -> list[dict]:
        data, _ = self.http.request("GET", "/v1/sandboxes", params={"status": status} if status else None)
        if isinstance(data, dict):
            return list(data.get("data") or [])
        return list(data or [])

    def destroy_all(self) -> int:
        """Destroy every live sandbox on the account (frees plan slots). Returns count."""
        n = 0
        for s in self.list():
            if s.get("status") not in ("destroyed", "destroying", "failed"):
                RestSandbox(self.http, s["id"]).destroy()
                n += 1
        return n

    def whoami(self) -> dict:
        data, _ = self.http.request("GET", "/v1/whoami")
        return data or {}


_default: RestSandboxFactory | None = None


def default_factory() -> RestSandboxFactory:
    global _default
    if _default is None:
        _default = RestSandboxFactory()
    return _default


def q(s: str) -> str:
    """Shell-quote helper for building exec() lines."""
    return shlex.quote(s)
