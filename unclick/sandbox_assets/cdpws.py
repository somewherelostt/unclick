"""Minimal stdlib-only CDP client (RFC 6455 websocket) for use inside the sandbox.

No third-party deps so it runs on any image with python3 >= 3.8.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse


class WS:
    def __init__(self, url: str, timeout: float = 30.0):
        u = urlparse(url)
        host, port = u.hostname, u.port or 80
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            buf += chunk
        head, self._rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket handshake failed: " + head.decode(errors="replace")[:300])

    def readable(self, timeout: float) -> bool:
        """True if a frame (or part of one) is waiting; never consumes data."""
        import select
        if self._rest:
            return True
        r, _, _ = select.select([self.sock], [], [], timeout)
        return bool(r)

    def _recv_exact(self, n: int) -> bytes:
        out = self._rest[:n]
        self._rest = self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(max(65536, n - len(out)))
            if not chunk:
                raise ConnectionError("websocket closed")
            out += chunk
        if len(out) > n:
            self._rest = out[n:] + self._rest
            out = out[:n]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(hdr) + masked)

    def recv(self) -> str:
        """Returns one complete text message (handles fragmentation, ping, close)."""
        parts: list[bytes] = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            payload = self._recv_exact(n) if n else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 0x8:
                raise ConnectionError("websocket closed by peer")
            if op == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
                continue
            if op == 0xA:
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def http_json(port: int, path: str, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


class CDP:
    """Request/response CDP over one websocket. Events are queued in self.events."""

    def __init__(self, ws_url: str, timeout: float = 30.0):
        self.ws = WS(ws_url, timeout=timeout)
        self.next_id = 0
        self.events: list[dict] = []

    def send(self, method: str, params: dict | None = None, session_id: str | None = None) -> int:
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        return self.next_id

    def call(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        mid = self.send(method, params, session_id)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} error: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def recv(self) -> dict:
        return json.loads(self.ws.recv())

    def close(self) -> None:
        self.ws.close()


def page_ws_url(port: int) -> str:
    """websocket URL of the first real page tab."""
    for t in http_json(port, "/json/list"):
        if t.get("type") == "page" and not t.get("url", "").startswith(("devtools://", "chrome-extension://")):
            return t["webSocketDebuggerUrl"]
    t = http_json(port, "/json/new?about:blank", method="PUT")
    return t["webSocketDebuggerUrl"]
