# Unclick

**Unclick any website into an API.**

Built for the CreateOS x Stripe Projects hackathon, *Build Agents That Run Code* (Oct 17, 2026).

## The problem

Half the web has no API. The data is on the screen, so a human can read it, but a developer can't call it. Scraping HTML breaks every time the markup changes. Most modern sites, though, fetch that data from a **hidden JSON API** that the page calls itself. That API is rarely documented, and you can only find it by opening DevTools and reading network traffic.

## What Unclick does

You give it a URL and a task, for example `https://projects.dev/leaderboard` and "Get the top projects with name, votes and URL". Then:

1. An AI agent does the task **once, live, in a real Chrome browser** that runs inside a [CreateOS](https://createos.sh) desktop sandbox. You watch it over noVNC.
2. A CDP recorder captures every JSON call the page makes. The pipeline picks the endpoint whose response actually contains the data that was on screen.
3. The agent **writes a Python client** for that endpoint, **runs it in a second sandbox, reads the traceback or the diff, and fixes it** until its output matches what the browser showed.
4. The verified client is **published as an HTTPS API** on the sandbox's public URL. Each call is billed in **Stripe test mode**, and the sandbox **auto-pauses** when idle and wakes on the next request.

The result is a `curl` command that returns clean JSON in about a second, with no LLM in the request path.

## How it works, step by step

| # | Phase | What happens | Code |
|---|---|---|---|
| 1 | **Boot** | A `desktop:1` sandbox starts with ingress on. Chrome launches with the DevTools protocol, and the network recorder starts. Egress is locked to the target site (`host`, `*.base`). | `unclick/desktop.py`, `unclick/pipeline.py` |
| 2 | **Explore** | The agent gets a screenshot, the page text, a list of clickable controls and a list of captured JSON calls. It then clicks, types, scrolls or picks a dropdown option. The pipeline refuses `done` (up to 3 times) until a captured JSON response really contains the on-screen data. If the page tried to call a host that egress blocks, exactly that host is added to the allowlist. | `unclick/pipeline.py`, `unclick/agent.py` |
| 3 | **Discover** | Captured JSON responses are scored by how many on-screen tokens they contain and filtered for noise (analytics, auth, feature flags). The model picks the endpoint from the top candidates, and the pipeline reads ground truth (the rows) off the screen. | `unclick/pipeline.py` |
| 4 | **Code, run, fix** | The desktop sandbox is destroyed. A `devbox:1` runner starts with egress locked to the API host. The agent writes `client.py`, the pipeline runs it with `exec`, and `verify_output` compares the stdout with the on-screen rows (numbers may drift a little on live sites). A crash, wrong JSON or a hard-coded value triggers a fix prompt with the stderr and the diff. Up to 5 attempts. | `unclick/pipeline.py`, `unclick/agent.py` |
| 5 | **Publish** | The verified sandbox is paused and forked (when the plan allows it) into an API sandbox with egress `[backend host, api.stripe.com]` and ingress on. A stdlib-only HTTP server and `client.py` are uploaded and started. `auto_pause_after_seconds=60` is set. | `unclick/publish.py`, `unclick/api_template/server.py` |
| 6 | **Bill and sleep** | Each successful call decrements prepaid credits and records a Stripe Billing Meter event. When idle, the sandbox auto-pauses, and the next request wakes it. | `unclick/billing.py`, `unclick/api_template/server.py` |

## Architecture

```
 Browser UI (app/static: vanilla JS + Tailwind)
    │  POST /api/runs {url, task}            GET /api/runs/{id}/events  (SSE)
    ▼
 Orchestrator (app/main.py, FastAPI)  ── mounts ──►  unclick/billing.py  (/api/billing/*: Stripe Checkout, usage)
    │  runs pipeline in a thread, forwards every EventSink event to the UI
    ▼
 unclick/pipeline.py:  boot → explore → discover → code/verify → publish
    │                       │                         │               │
    │            unclick/agent.py (LLM:       unclick/desktop.py   unclick/publish.py
    │            tool calls + vision)         Chrome + CDP         pause → fork → upload → start
    │                       │                 recorder + noVNC            │
    ▼                       ▼                         ▼               ▼
 unclick/sandbox.py  ── CreateOS Sandbox REST API (httpx) ────────────────────────────┐
                                                                                       │
   ┌──────────────────────────────┐   verified    ┌──────────────────────────────┐    │
   │ desktop:1 sandbox            │   client.py   │ devbox:1 runner  ──fork──►    │◄───┘
   │ Chrome + CDP + noVNC         │ ────────────► │ API sandbox (public URL)      │
   │ egress: target site          │               │ server.py + client.py         │
   └──────────────────────────────┘               │ egress: backend + Stripe      │
                                                  │ auto-pause 60 s, wake-on-req  │
 curl -H "X-Unclick-Key: ..." https://<id>-8000.<domain>/v1/run ────────────────► └──────────────┬───────────────┘
                                                                                                 │ meter event + usage
                                                                                                 ▼
                                                                                      Stripe (test mode)
```

All progress goes through one `EventSink.emit(kind, message, **data)` contract ([unclick/contracts.py](unclick/contracts.py)). The UI is a pure renderer of those events, which is also why the scripted demo mode works.

## CreateOS Sandbox features used

| Feature | Where it is used | Status |
|---|---|---|
| **`desktop:1` image + noVNC live view** | `create_desktop()` in [unclick/desktop.py](unclick/desktop.py) boots the sandbox, starts Chrome and gets the noVNC URL from the computer API (`/screens/screen-0/connect`). The UI embeds it in an iframe. Clicks, typing and screenshots also use the computer API. | Verified live (boot, Chrome, recorder, explore) |
| **Egress allowlist** | The desktop starts locked to the target site. `_allow_host()` in [unclick/pipeline.py](unclick/pipeline.py) adds a host live when the page's fetch is blocked, or when the picked API lives on another host. The runner is locked to the API host. The published API is locked to `[backend host, api.stripe.com]`. | Verified live for set/update. On projects.dev the API is on the same host, so no host is *added* in that demo (see Limitations). |
| **Exec self-fix loop** | The runner uses `sandbox.exec` to run `client.py` and returns stdout, stderr and the exit code to the agent, which patches the code. See `run()` in [unclick/pipeline.py](unclick/pipeline.py). | Verified live: client verified on attempt 2 in a real run |
| **Fork** | `publish()` pauses the verified sandbox, then `fork(egress=..., ingress=True)` creates the API sandbox with the same warm state and a tighter policy. | Used when the plan allows it (Beginner or higher). On Free, the fork is refused (concurrent quota 1), and it falls back to publishing in the single sandbox. **Not yet verified live.** |
| **Ingress public URL** | `enable_ingress()` and `preview_url(8000)` give `https://<id>-8000.app.sb.createos.sh`, which serves `/v1/run`, `/health` and `/docs`. | Verified live |
| **Pause/resume + wake-on-request** | `set_auto_pause(60)` is set at publish. The next request to the URL wakes the sandbox, and `wake_and_time()` measures the latency. | Verified live: auto-paused after about 61 s, woke in about 6 s |

## How Stripe is used

**Stripe Projects** provisions the stack from the terminal. The CreateOS sandbox account and its credentials come from `stripe projects add createos/project`, and the optional LLM path comes from `openrouter/api` (OpenAI-compatible, so set `LLM_BASE_URL=https://openrouter.ai/api/v1`). We share the stack on the projects.dev leaderboard with `stripe projects share`. See [docs/stripe-projects.md](docs/stripe-projects.md) for the setup notes.

**Stripe test-mode billing** turns the API into a paid product ([unclick/billing.py](unclick/billing.py), [unclick/api_template/server.py](unclick/api_template/server.py)):

- A Stripe **Price** ($5 for 100 calls by default) and a **Billing Meter** (`unclick_api_call`) are created on first use. Both are idempotent.
- **Buy 100 calls** in the UI opens a Stripe **Checkout** session in test mode. After a verified payment, the app issues an API key `uk_<customer id>_<hex>`. Only a SHA-256 of the key is stored, in the Stripe Customer's metadata.
- Every successful call to `/v1/run` uses one credit and sends a **meter event** to Stripe from inside the sandbox. This happens on a background thread, so the response isn't slowed down. Failed client calls are refunded, not charged. Out of credits returns **HTTP 402** with a buy link.
- Only `sk_test_` / `rk_test_` keys are accepted, and live keys are refused. No real money moves.
- A free **demo key** is issued at publish time so the live demo never blocks. Its calls are still metered to a shared demo customer.

## Run locally

Requires Python 3.12+.

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your keys; never commit .env
uvicorn app.main:app --port 8080
```

Open http://localhost:8080, keep the default URL and task, and press **Unclick it**.

**Scripted demo, no keys needed:**

```bash
UNCLICK_FAKE=1 uvicorn app.main:app --port 8080     # PowerShell: $env:UNCLICK_FAKE="1"; uvicorn app.main:app --port 8080
```

This replays a scripted run of about 75 seconds (`UNCLICK_FAKE_SPEED=4` makes it 4x faster) that exercises every part of the UI. The sandboxes, the data and the Stripe charge in this mode are **simulated** and don't touch CreateOS, an LLM or Stripe.

**Command line (no UI):**

```bash
python scripts/run_agent.py                      # real run on projects.dev/leaderboard
python scripts/run_agent.py --fake --scripted    # fully offline
```

### Environment variables

| Variable | Purpose |
|---|---|
| `CREATEOS_SANDBOX_API_KEY` / `CREATEOS_API_KEY` | CreateOS sandbox key (from Stripe Projects or the CreateOS dashboard) |
| `CREATEOS_SANDBOX_BASE_URL` | defaults to `https://api-eu.sb.createos.sh` |
| `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | the agent's LLM (default `gpt-4.1`, needs vision and tool calling; optional OpenRouter base URL) |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` | Stripe **test** keys. Without them, the API runs unmetered with a local demo key. |
| `PUBLIC_BASE_URL` | public URL of the orchestrator, used for Checkout redirects |
| `UNCLICK_FORK=0` | skip the fork attempt (saves about 7 s on the Free plan) |
| `UNCLICK_FAKE=1` | scripted demo mode |

### Plans and limits

The Free CreateOS plan allows 1 concurrent sandbox and 10 sandboxes per day. Fork needs a second sandbox, and a desktop wants more than 1 vCPU, so use the **Beginner** plan (5 concurrent) for the full flow.

## Tech stack

- **CreateOS Sandbox**: `desktop:1` and `devbox:1` microVMs, noVNC, egress allowlists, fork, ingress URLs, pause/resume with wake-on-request
- **Stripe Projects** (provisioning) and **Stripe** test-mode Checkout, Customers and Billing Meters
- **LLM**: OpenAI-compatible tool calling and vision (OpenAI directly, or OpenRouter)
- **Python 3.12**, FastAPI, Server-Sent Events, httpx, pydantic
- The published API server is **stdlib-only Python** (no pip, no extra egress)
- Chrome DevTools Protocol for the network recorder
- Vanilla JS + Tailwind for the UI

## Limitations (honest notes)

- **Public data only.** Unclick doesn't bypass CAPTCHAs, bot detection or paywalls. The demo target is a public leaderboard, and we send very few requests.
- **projects.dev/leaderboard is server-rendered.** Its JSON API (`/api/leaderboard`) fires only when the "filter by hackathon" dropdown changes, so the agent has to change the filter and then restore it. The API is on the same host, so the live "host added to egress" moment appears only on sites whose API lives on another host.
- **Fork and Stripe flows have not been verified end to end live.** The fork path has been exercised offline, and live on Free it is refused (quota) and falls back to a single sandbox. The Stripe paths are covered by offline tests, and the live checks are still to do.
- Computer use can be flaky, and the agent can fail on sites with heavy client-side state or login walls.
- The published API runs one generated client. Parameters beyond what the client's `run(**params)` accepts aren't inferred automatically.

## Repo layout

```
app/                 FastAPI orchestrator + UI (static/), scripted demo (fake_pipeline.py)
unclick/             contracts, pipeline, agent, prompts, sandbox + desktop (CreateOS), publish, billing
unclick/api_template server.py: the stdlib API server that runs inside the published sandbox
scripts/             run_agent.py, smoke tests
docs/                PLAN.md, DEMO_SCRIPT.md, SUBMISSION.md, research notes
```

## Team

<!-- Fill in before submitting -->
| Name | Role | Discord / handle |
|---|---|---|
| _TBD_ | _TBD_ | _TBD_ |

Team name: _TBD_
