# Unclick: build plan

> **Unclick any website into an API.**
> You do the task once. An agent repeats it live in Chrome inside a CreateOS desktop sandbox while capturing the site's hidden JSON calls. It then writes a Python client, runs it, and fixes it until the output matches the screen. Finally it publishes the client as a paid, auto-pausing HTTPS API on the sandbox's public URL.

Deadline: **submissions lock 5:30 PM PT (6:00 AM IST)**. Code freeze for features is **4:15 PM PT (4:45 AM IST)**. After that we only fix bugs and work on the video, README and post.

## Demo target (real site, no mocks)
- Primary: **https://projects.dev/leaderboard**. It renders client-side, so a hidden JSON API exists. Task: "Get the top projects with name, votes and URL."
- Backup: the YC company directory (https://www.ycombinator.com/companies), which also renders client-side via a search API.
- Rules: public data only. No CAPTCHA, anti-bot or paywall bypass. Respect the site; don't hammer it.

## Architecture (Python 3.12, one repo)

```
browser UI (app/static/index.html, vanilla JS + Tailwind CDN)
   │  POST /api/runs {url, task}        GET /api/runs/{id}/events  (SSE)
   ▼
orchestrator (app/main.py, FastAPI)  ── calls ──► unclick/pipeline.py
                                                     │
          ┌──────────────────────────────────────────┼─────────────────────────┐
          ▼                                          ▼                         ▼
  unclick/agent.py (OpenAI loop)          unclick/desktop.py           unclick/publish.py + billing.py
  explore → discover → code → verify      Chrome + CDP recorder        API server in sandbox, Stripe test mode
          │                                          │
          └──────────────► unclick/sandbox.py (CreateOS REST client, httpx) ◄─────┘
```

### CreateOS features we use (each has a visible moment in the demo)
| Feature | Where | Demo moment |
|---|---|---|
| `desktop:1` + noVNC live view | explore phase | Judges watch Chrome click through the site live |
| **Egress allowlist** | whole run | Starts locked to `projects.dev`. When the agent finds the hidden backend host, the UI shows `+ api.example.com` added. The published API is locked to `[backend host, api.stripe.com]` |
| Run code | code/verify loop | Agent writes `client.py`, runs it, reads the traceback, fixes it. The attempt counter is visible |
| **Fork** | verified to published | Pause the verified sandbox, then fork the API sandbox with a different egress policy and ingress on. Same warm state, tighter policy |
| **Ingress / live URL** | publish | `curl https://<id>-8000.<domain>/v1/run` returns JSON |
| **Pause / resume + wake-on-request** | after publish | `auto_pause_after_seconds=60`. UI shows "paused". The next curl wakes it, and we show the latency |

Free plan = 1 concurrent sandbox. Fork plus desktop needs **Beginner** (5 concurrent, 4 vCPU). Fallback on Free: one sandbox does everything (explore, code, publish on its own ingress), and fork is skipped.

### Stripe usage
- **Stripe Projects** provisions the stack (`createos/project`, `openrouter/api` as an optional LLM path). Commit `.projects/state.json`, but never the vault or `.env`.
- **Stripe test mode billing**: the user buys API calls through Stripe Checkout (test) and gets an API key. Each call to the published API records a usage/meter event, or decrements prepaid credits. Owner: billing session.

## Env vars (`.env`, never committed; see `.env.example`)
`CREATEOS_SANDBOX_API_KEY`, `CREATEOS_API_KEY` (same value), `CREATEOS_SANDBOX_BASE_URL=https://api-eu.sb.createos.sh`, `OPENAI_API_KEY`, `LLM_MODEL` (default `gpt-4.1`), `LLM_BASE_URL` (optional, for OpenRouter), `STRIPE_SECRET_KEY` (sk_test_...), `STRIPE_PUBLISHABLE_KEY`, `PUBLIC_BASE_URL` (orchestrator URL for Checkout redirects).

## File ownership (one owner per file, so no two sessions edit the same file)
| Session | Owns |
|---|---|
| **sandbox-core** | `unclick/sandbox.py`, `unclick/desktop.py`, `unclick/sandbox_assets/*`, `scripts/smoke_*.py` |
| **agent-loop** | `unclick/agent.py`, `unclick/prompts.py`, `unclick/pipeline.py`, `scripts/run_agent.py` |
| **publish-billing** | `unclick/publish.py`, `unclick/billing.py`, `unclick/api_template/*`, `scripts/smoke_publish.py` |
| **web-ui** | `app/*` (main.py, static/, fake_pipeline.py) |
| **orchestrator (me)** | `unclick/contracts.py`, `requirements.txt`, `.env.example`, `.gitignore`, `README.md`, `docs/*` |

Need a change to `contracts.py` or `requirements.txt`? Add only the lines you need, keep existing signatures stable, and say so in your final report.

## Contracts: see `unclick/contracts.py` (source of truth)
- `Sandbox`, `Desktop`: implemented by sandbox-core. Everyone else codes against the Protocol, and agent-loop and web-ui can use fakes until it lands.
- `EventSink.emit(kind, message, **data)`: every component reports progress through this, and the UI renders it. The event kinds and data fields are listed in `contracts.py` and must match exactly.
- `pipeline.run(url, task, sink) -> RunResult`: implemented by agent-loop and called by `app/main.py`.
- `publish.publish(source_sandbox, client_code, sink, name) -> PublishedApi`: implemented by publish-billing and called by the pipeline.

## Timeline (PT)
- 1:00 to 1:15: scaffold + contracts (orchestrator)
- 1:15 to 3:15: four sessions build in parallel against the contracts
- 3:15 to 4:15: integration on projects.dev, end to end. Deploy the orchestrator (a CreateOS devbox sandbox with ingress, or Stripe Projects hosting)
- 4:15 to 5:00: record the 2 to 3 min video, README, X/LinkedIn post, leaderboard (`stripe projects share`, code `CreateOS-2026`)
- 5:00 to 5:30: submit the form, then freeze

## Demo script (2:30)
1. (15s) On camera: "Half the web has no API. Unclick turns any site into one."
2. (60s) Paste projects.dev/leaderboard plus the task. Live Chrome clicks, and the egress allowlist grows when the hidden API is found.
3. (45s) The agent writes `client.py`, the first run fails, it reads the error, fixes it, and the output matches the screen.
4. (20s) Fork, then a public URL. `curl` returns JSON in under a second, and the Stripe test-mode charge shows up.
5. (10s) The sandbox auto-pauses, the next curl wakes it, and we show the latency. Close with how Stripe Projects + CreateOS were used.
