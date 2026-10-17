"""Shared contracts for Unclick. Source of truth for every session.

Change policy: add, don't break. Keep existing names and signatures stable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# ---------------------------------------------------------------------------
# Events: every component reports progress through EventSink; the UI renders them.
# ---------------------------------------------------------------------------

EventKind = Literal[
    "status",          # {phase: "boot"|"explore"|"discover"|"code"|"verify"|"publish"|"live"|"failed"}
    "sandbox",         # {id, role: "desktop"|"api"|"runner", action: "created"|"forked"|"pausing"|"paused"|"resumed"|"destroyed", ms?: float, forked_from?: str}
    "live_view",       # {url}  noVNC URL of the desktop sandbox (embed in an iframe)
    "egress",          # {allow: list[str], added?: list[str], reason}
    "browser_action",  # {action: "open"|"click"|"type"|"press"|"scroll"|"wait", detail: str}
    "screenshot",      # {png_b64}
    "capture",         # {url, method, status, content_type, size}  one per interesting JSON response
    "endpoint",        # {url, method, why}  the hidden API the agent picked
    "code",            # {attempt: int, filename: str, code: str}
    "run",             # {attempt: int, exit_code: int, stdout: str, stderr: str, ms: float}
    "verify",          # {attempt: int, ok: bool, expected: str, got: str, notes: str}
    "publish",         # {api_url, docs_url, curl, sandbox_id, api_id, openapi_url, mcp_url, mcp_config, tool_name,
                       #  schema, fields: [{name, type, required}]}
    "health",          # {api_id, ok, checked_at, problems: list[str], healthy, last_verified}
    "billing",         # {event: "checkout_created"|"paid"|"call_charged", checkout_url?, api_key?, calls_remaining?, amount_cents?}
    "pause",           # {state: "paused"|"waking"|"awake", wake_ms?: float}
    "log",             # {level: "info"|"warn"}  free-form detail
    "error",           # {where: str}
    "done",            # {summary, api_url?}
]


@dataclass
class Event:
    kind: EventKind
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "data": self.data, "ts": self.ts}


class EventSink(Protocol):
    def emit(self, kind: EventKind, message: str, **data: Any) -> None: ...


class PrintSink:
    """Default sink for scripts/tests: prints events to stdout."""

    def emit(self, kind: EventKind, message: str, **data: Any) -> None:
        short = {k: (v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v) for k, v in data.items()}
        print(f"[{kind}] {message} {short if short else ''}", flush=True)


# ---------------------------------------------------------------------------
# Sandbox layer (implemented by sandbox-core in unclick/sandbox.py, unclick/desktop.py)
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    ms: float = 0.0


@dataclass
class CapturedRequest:
    url: str
    method: str
    status: int
    content_type: str
    request_headers: dict[str, str]
    request_body: str | None
    response_headers: dict[str, str]
    response_body: str  # truncated to ~200 KB by the recorder
    seq: int = 0        # monotonically increasing capture index


class Sandbox(Protocol):
    id: str

    def exec(self, cmd: str, timeout: int = 120, env: dict[str, str] | None = None) -> ExecResult:
        """Run a shell line (`sh -c cmd`) and wait for it."""

    def start_process(self, cmd: str) -> str:
        """Start a long-running background process (e.g. uvicorn); returns a process id."""

    def write_file(self, path: str, content: str | bytes) -> None: ...

    def read_file(self, path: str) -> bytes: ...

    def get_egress(self) -> list[str]: ...

    def set_egress(self, allow: list[str]) -> None:
        """Full replace. [] means open. Any non-empty list is deny-by-default."""

    def enable_ingress(self) -> None: ...

    def preview_url(self, port: int) -> str:
        """Public HTTPS URL for a server bound to 0.0.0.0:port."""

    def set_auto_pause(self, seconds: int) -> None: ...

    def status(self) -> str:
        """creating|running|pausing|paused|resuming|forking|error|destroying|destroyed"""

    def pause(self) -> None:
        """Pause and block until status == paused."""

    def resume(self) -> None:
        """Resume and block until status == running."""

    def fork(self, egress: list[str] | None = None, ingress: bool | None = None,
             envs: dict[str, str] | None = None) -> "Sandbox":
        """Source must be paused. Blocks until the child is running and returns its handle."""

    def destroy(self) -> None: ...


class SandboxFactory(Protocol):
    def create(self, *, rootfs: str = "devbox:1", shape: str = "s-1vcpu-1gb",
               egress: list[str] | None = None, ingress: bool = False,
               envs: dict[str, str] | None = None, name: str | None = None) -> Sandbox: ...


class Desktop(Protocol):
    """A desktop:1 sandbox running Chrome with CDP and a network recorder."""

    sandbox: Sandbox

    def live_view_url(self) -> str:
        """noVNC URL for the UI iframe (requires ingress)."""

    def open(self, url: str) -> None: ...

    def screenshot(self) -> bytes:
        """PNG bytes of the current screen."""

    def click(self, x: int, y: int) -> None: ...

    def type(self, text: str) -> None: ...

    def press(self, keys: list[str]) -> None: ...

    def scroll(self, dy: int) -> None: ...

    def page_text(self) -> str:
        """Visible text of the current page (via CDP), truncated to ~20 KB."""

    def captured_requests(self, since_seq: int = 0, json_only: bool = True) -> list[CapturedRequest]: ...


# ---------------------------------------------------------------------------
# Pipeline + publish (agent-loop and publish-billing)
# ---------------------------------------------------------------------------


@dataclass
class PublishedApi:
    api_url: str          # e.g. https://<id>-8000.<domain>/v1/run
    docs_url: str         # FastAPI /docs on the same host
    curl: str             # copy-paste example including the API key header
    sandbox_id: str
    openapi_url: str = ""  # OpenAPI 3.1 spec with the inferred response schema
    mcp_url: str = ""      # MCP Streamable-HTTP endpoint (one tool), same key + price as api_url
    mcp_config: str = ""   # ready-to-paste {"mcpServers": {...}} JSON for Claude Desktop / Cursor


@dataclass
class RunResult:
    ok: bool
    endpoint: str | None
    client_code: str | None
    sample_output: Any
    attempts: int
    api: PublishedApi | None = None
    error: str | None = None
