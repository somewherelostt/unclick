# CreateOS Sandbox: developer reference (hackathon)

Legend: **[V-docs]** read from official docs (https://createos.sh/docs/llms-full.txt, fetched 2026-10-17); **[V-live]** observed against the live API without a key; **[V-pkg]** checked on npm/PyPI; **[INF]** inferred, test before relying on it.
Product: one Firecracker microVM (own kernel) per sandbox. Docs index: https://createos.sh/docs/Sandbox/Overview

## 0. Base URL and live probes
- Organizers' base: `https://api-eu.sb.createos.sh` (EU control plane). Docs say `https://api.sb.createos.sh`. Docs: `region` is `eu` or `us` and "must match the control plane you are talking to" [V-docs]. Use the base URL your key was issued for; set it via `CREATEOS_SANDBOX_BASE_URL` / `baseUrl`.
- [V-live] Every path without a key returns `401 {"status":"fail","data":{"auth":"invalid api key"}}` (/, /docs, /openapi.json, /swagger, /health, /v1/whoami). No OpenAPI/Swagger is served unauthenticated.
- [V-live] Public (no key): `GET /v1/shapes`, `GET /v1/rootfs` (200 on both hosts). Rate-limit headers: 300 req per 60 s (`x-ratelimit-*`).
- All paths are under `/v1`. Responses are JSend: `{"status":"success","data":{...}}`; `fail` = 4xx, `error` = 5xx. Lists: `data.data[]` plus `data.pagination`; `limit` default 50, max 500 [V-docs].

## 1. Auth
- Header `X-Api-Key: <token>` [V-docs]. Also accepted: `X-Auth-Token` (session token), `X-Access-Token` (OAuth). WebSocket shell also accepts `?token=` [V-docs].
- Key source: dashboard https://createos.sh/app/profile. CLI: `createos login` (browser) or `createos login --token <t>` [V-docs]. Hackathon path: `stripe projects add createos/project` creates the account and writes credentials to `.env`; `stripe projects env` shows them [V-docs]. The exact env var names Stripe writes are NOT documented: run `stripe projects env`, then map them to the SDK variables below. [INF]
- Delegated per-sandbox token: prefix `skp_sb_`, sent in `X-Api-Key`, can operate only that sandbox (Python: `sandbox.create_access_token()`) [V-docs].
- Check a key: `GET /v1/whoami` -> `{user_id, stats:{running,paused,other,total}}` [V-docs].

## 2. SDKs [V-docs; versions V-pkg]
| Lang | Install | Env vars |
|---|---|---|
| TypeScript `@nodeops-createos/sandbox` (v0.8.2) | `npm i @nodeops-createos/sandbox` (Node 20+) | `CREATEOS_SANDBOX_API_KEY`, `CREATEOS_SANDBOX_BASE_URL` |
| Python PyPI `createos-sandbox` (v0.1.3), import `createos` | `pip install createos-sandbox` (3.10+) | `CREATEOS_API_KEY`, `CREATEOS_SANDBOX_BASE_URL` |
| Go `github.com/NodeOps-app/createos-go-sdk`; Rust `createos`; C# `CreateOS.Sandbox`; Java `sh.createos:createos-java-sdk` | see docs/Sandbox/SDK/Overview | `CREATEOS_API_KEY` |

Repos: github.com/NodeOps-app/createos-sandbox-sdk (TS), createos-python-sdk, createos-go-sdk. CLI: `curl -sfL https://raw.githubusercontent.com/NodeOps-app/createos-cli/main/install.sh | sh -`.
```ts
import { createClient } from "@nodeops-createos/sandbox";
const client = createClient({ apiKey: process.env.CREATEOS_SANDBOX_API_KEY!, baseUrl: "https://api-eu.sb.createos.sh" });
const sb = await client.createSandbox({ shape: "s-1vcpu-1gb", rootfs: "devbox:1", egress: ["pypi.org:443", "*.pythonhosted.org:443"] });
try {
  await sb.files.upload("/tmp/a.py", "print(1+1)\n");
  const { result } = await sb.runCommand("python3", ["/tmp/a.py"]);
  console.log(result.stdout, result.exit_code);
} finally { await sb.destroy(); }
```
```python
from createos import Client, CreateSandboxRequest, RunCommandRequest
with Client(api_key=KEY, base_url="https://api-eu.sb.createos.sh") as c:
    sb = c.create_sandbox(CreateSandboxRequest(shape="s-1vcpu-1gb", rootfs="devbox:1"))
    try:
        print(sb.run_command(RunCommandRequest(command="python3", arguments=["-c", "print(2)"])).result.standard_output)
    finally:
        sb.destroy()
```
TS handle [V-docs]: `runCommand`, `streamCommand` (async iterator: stdout/stderr/exit/error/heartbeat), `sh`, `files.upload/download`, `pause/resume/fork`, `waitUntilRunning/Paused/Destroyed`, `setAutoPause`, `setIngress`, `waitForPortReady`, `previewUrl`, `getEgress/setEgress`, `processes.*`, `computer.*`. Python [V-pkg README]: `pause().wait_until_paused()`, `fork()`, `resume().wait_until_running()`, `wait_for_port`, `preview_url`, `files`, `processes`, `computer`. The Python egress method name is unconfirmed; use REST `PUT /egress` if unsure. [INF]

## 3. Endpoints (REST) [V-docs unless noted]
### Create: `POST /v1/sandboxes`
Body: `shape` (required), `rootfs`, `name` (unique per user; also hostname/DNS), `disk_mib`, `envs{}` (<=64 keys, 4 KiB/value, write-only), `ingress_enabled`, `egress[]`, `auto_pause_after_seconds` (60-86400), `networks[{id}]`, `disks[]`, `ssh_pubkeys[]`, `host_id`, `region`.
Resp: `{id:"sb-<ulid>", name, ip, shape, rootfs, vcpu, mem_mib, disk_mib, spawn_ms, egress[], bandwidth_quota_bytes}`.
Images [V-live]: `devbox:1` (Ubuntu 24.04, Python 3.12 + pandas/numpy/matplotlib/sklearn, Node 24, Bun, Go, Rust, Docker, SSH, preinstalled `claude`, `codex`, `opencode`, `pi`, `cursor-agent`), `desktop:1` (XFCE + Chrome + computer-use), `ubuntu:26.04`, `debian:13`, `alpine:3.20`. **Catalog default is `alpine:3.20` [V-live]: pass `rootfs:"devbox:1"` explicitly.**
Shapes [V-live]: `s-0.25vcpu-512mb`, `s-0.5vcpu-1gb`, `s-1vcpu-256mb|1gb|2gb`, `s-2vcpu-2gb|4gb`, `s-4vcpu-4gb|8gb`, `s-8vcpu-8gb|16gb`. Largest allowed depends on plan (Free: 1 vCPU / 1 GB).
Others: `GET /v1/sandboxes?status=&limit=&offset=`, `GET /v1/sandboxes/{id}`, `PATCH /v1/sandboxes/{id}` (`ingress_enabled`, `auto_pause_after_seconds`, `disable_auto_pause`), `GET /v1/sandboxes/by-ip/{ip}`.
### Delete: `DELETE /v1/sandboxes/{id}` -> `{id, status:"destroying"}`; idempotent. Always call in `finally`.
### Run command: `POST /v1/sandboxes/{id}/exec`
Body `{cmd, args[], stdin, env{}, stream}`. `cmd` is a program, not a shell line: use `{"cmd":"sh","args":["-c","..."]}`. `env` may only override keys declared in `envs` at create (else 400).
Buffered resp: `{"data":{"result":{"stdout","stderr","exit_code"},"exec_ms":124.7}}`; buffered output capped at 1 MiB.
Background jobs: plain `&` blocks unless stdio is redirected (`nohup setsid cmd >/tmp/x.log 2>&1 &`); prefer managed processes.
### Streaming output: `POST /v1/sandboxes/{id}/exec?stream=true` (or `"stream":true`)
`application/x-ndjson`, one JSON per line: `{"stdout":"..."}`, `{"stderr":"..."}`, `{"hb":true}` every 5 s, last frame `{"exit_code":N}`, or `{"error":"..."}`. Client disconnect kills the command in ~5 s.
### Managed processes (long-running, reconnectable logs)
`POST /v1/sandboxes/{id}/processes` `{cmd,args,cwd,env,pty:{rows,cols}}` -> `{process_id:"proc_...",pid,state,exit_code,output:{oldest_seq,newest_seq,bytes}}`. Then `GET .../processes`, `GET .../processes/{pid}`, `GET .../{pid}/connect?after=<seq>` (NDJSON replay + follow, data base64), `POST .../input`, `.../signal`, `.../resize`, `.../stdin/close`, `GET .../wait` (long-poll <=30 s), `DELETE .../{pid}`. Retained output: 1 MiB per process, 32 MiB per sandbox (oldest dropped; HTTP 410 if offset evicted).
### Files
`PUT /v1/sandboxes/{id}/files?path=/abs/path` raw bytes (`application/octet-stream`, up to 10 GiB, parents auto-created, relative path -> 400). `GET .../files?path=/abs/path` returns raw bytes. No list/mkdir endpoint is documented: use exec (`ls`, `tar`). Directories: tar, upload, then `tar -xf` via exec.
### Live URL / port exposure
Create with `ingress_enabled:true` (or `PATCH {"ingress_enabled":true}`). Any server bound to **0.0.0.0** (not 127.0.0.1) on any port is reachable at `<ulid>-<port>.<ingress-domain>`. The concrete domain is NOT in the docs; read `ingress_url_template` (literal `<port>` placeholder) from `GET /v1/sandboxes/{id}`, or use `sandbox.previewUrl(port)` / `preview_url(port)`. [V-docs for template; domain unknown]
Gotcha: SDK `previewUrl` defaults to `https`; docs advise `{scheme:"http"}` when the TLS cert is not provisioned yet. Wake-on-request: hitting a paused sandbox's URL resumes it (browser sees a waiting page). Inbound is closed by default.
No-ingress alternatives: `POST /v1/sandboxes/{id}/tunnel/{port}` (HTTP Upgrade `tcp-tunnel`, 101); CLI `createos sandbox tunnel --remote 3000 --local 3000 <id>`; `POST .../shell`, `GET .../shell-ws` (WebSocket PTY); SSH gateway `gateway.sb.createos.sh:2222` with attached `ssh_pubkeys`.
### Pause / resume (async: 202 + `X-Poll-After` header)
- `POST /v1/sandboxes/{id}/pause` (must be `running`, else 409) -> `{id, status:"pausing"}`; poll `GET /v1/sandboxes/{id}` until `paused`. Snapshots full RAM + CPU + disk. vCPU billing stops (memory + storage still billed).
- `POST /v1/sandboxes/{id}/resume` (must be `paused` or `error`) -> `resuming` -> `running`. 409 if running; 429 plan cap full; 402 no credit; 503 no capacity. Same id; processes continue where they left off; reconnect external sockets. Resume ~640-790 ms (from a docs search snippet, unconfirmed).
- Auto-pause: `auto_pause_after_seconds` 60-86400 (off by default). Self-pause from inside, no key: `POST http://127.0.0.1:1029/self/pause?reason=x` (also `/self/delete`; FIFO: `echo park > /run/self`).
### Fork: `POST /v1/sandboxes/{id}/fork`
**Source must be `paused`** (409 otherwise; SDK throws a validation error). Body, all optional: `start_paused`, `ssh_pubkeys[]`, `egress[]`, `ingress_enabled`, `envs{}` (non-empty map replaces inherited env). Resp 202: `{id:"sb-NEW", name, status:"forking", shape, vcpu, mem_mib, disk_mib, forked_from}`; poll the NEW id until `running` (or `paused` if `start_paused`). Child is a byte-identical snapshot (memory + disk) with new id/IP/placement/bandwidth ledger; parent stays `paused`. `bandwidth_quota_bytes` in body -> 400. S3 disks are not inherited. Per-fork `egress`/`envs` overrides enable "same warm state, different policy/secrets" demos.
Recipe: `pause -> wait paused -> fork xN -> (resume parent) -> exec in each child -> destroy all`. CLI: `createos sandbox fork <id> [--paused] [--egress host]`.
### Egress control / network policy
- **Empty, omitted or `["*"]` = open. Any non-empty list = deny-by-default**, enforced in-kernel on the host outside the VM (the guest cannot change it). Rules apply live, no restart.
- Rule forms: `host`, `host:port`, `*.host`, `ip`, `ip:port`, `cidr`, `cidr:port`, `*`. No denylist. Set at create so it is active before the first command.
- `GET /v1/sandboxes/{id}/egress` -> `{id, egress[]}`; `PUT /v1/sandboxes/{id}/egress` `{"egress":[...]}` (full replace; `[]` reopens). Allow time for hostname rules to propagate.
- Python job needs `["pypi.org:443","*.pythonhosted.org:443"]`. Docs are inconsistent: one table says a `host` rule allows TCP 80/443 and `host:port` is limited to those two ports (use IP/CIDR for others), prose says any port. [V-docs; test non-443 ports]
- CLI: `createos sandbox firewall show|set|clear <id>`. Private networks (`/v1/networks`, `POST /v1/sandboxes/{id}/networks`) are NOT governed by egress rules.
### Desktop / computer use: create with `rootfs:"desktop:1"`
Docs example uses `s-4vcpu-4gb`; Free plan caps at 1 vCPU/1 GB, so desktop size on Free is untested. [INF]
Base `/v1/sandboxes/{id}/computer`: `GET /screenshot` (PNG; opt `screen_id`, `window_id`, `x,y,width,height`), `/screen`, `/cursor`, `GET|PUT /clipboard`, `POST /open {target}`, `POST /launch {application,uri}`; `POST /mouse/{move|click|scroll|drag|down|up}`, `POST /keyboard/{type|press|down|up}` (`{text,delay_in_ms}`, `{keys:["ctrl","a"]}`); windows: `GET /windows`, `POST /windows/{w}/{focus|move|resize|maximize|minimize|restore}`, `DELETE /windows/{w}`; screens `screen-0`..`screen-7`: `GET|POST /screens`, `GET /screens/{s}/connect` -> `{screen_id,port,path,token,expires_at,url}` (noVNC in a browser; **requires `ingress_enabled:true`, else 409**; token is a credential). Errors: 429 screenshot busy, 501 no desktop tools (wrong image), 409 desktop unavailable. SDK: `sandbox.computer.screenshot()/mouse.click()/keyboard.type()/screens.connect()`.
### Other
Templates (`POST /v1/templates`, Dockerfile -> custom rootfs; not on Free), Disks (S3 mounts; not on Free), Networks, `/v1/sandboxes/{id}/bandwidth[/recharge]`, resize, metrics, webhooks page (documents lifecycle polling as the alternative), `/v1/devices` (WireGuard VPN).

## 4. Limits, plans, gotchas
- Plans [V-docs]: Free = **1 concurrent sandbox, 10 per rolling day**, 1 vCPU/1 GB max, 10 GiB disk, no disks/templates. Beginner (5 concurrent, 50/day, 4 vCPU/4 GB), Pro (20/200, 8 vCPU/8 GB), Enterprise (30/300). Over cap => **429**, no queue. Change plan: `stripe projects update createos-plan --service <beginner-monthly|enterprise-monthly|free>` or `stripe projects add createos/pro-monthly`.
- **Paused sandboxes count toward the concurrency cap; fork needs a paused parent plus a child**, so on Free (cap 1) fork or parallel demos will 429. Plan for Beginner+. [counting is V-docs; the fork-on-Free consequence is INF]
- New accounts: 500 free credits. Per-second billing while running: $0.0362/vCPU-h, $0.0116/GiB-RAM-h, $0.000158/GB-h storage, no egress fees; 402 when credit runs out.
- No max lifetime. Auto-pause is OFF by default: unattended boxes bill until destroyed. Destroy is the only way to free a plan slot.
- Time to first command ~210 ms median (built-in images are kept warm); custom templates are slow on first boot per host.
- Bandwidth: 50 GiB per sandbox default; networking stops when exhausted (recharge endpoint).
- Statuses: creating, running, pausing, paused, resuming, forking, error (POST /resume retries), destroying, destroyed, failed. Poll after every 202 (honor `X-Poll-After`); do not exec until `running`. Exec on a non-running sandbox returns 404/409.
- Servers must bind 0.0.0.0 for ingress; `waitForPortReady` probes loopback, so it passes even if you bound wrongly.
- Not found in docs: an OpenAPI spec URL, the ingress domain, the Stripe-written env var names. Everything not marked V-live was not exercised with a real key.
Sources: https://createos.sh/docs/llms-full.txt (entire docs), https://createos.sh/docs/Sandbox/Quickstart, .../Sandbox/REST-API/Overview, .../Sandbox/Limits, https://createos.sh/hackathon, https://pypi.org/project/createos-sandbox/, https://www.npmjs.com/package/@nodeops-createos/sandbox.
