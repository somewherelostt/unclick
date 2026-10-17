# Unclick

**Turn browser workflows into reusable APIs.**

Many websites have no public API. Unclick sends an AI agent into a real Chrome browser inside a [CreateOS](https://createos.sh) desktop sandbox. It captures the site's JSON requests, writes a Python client, runs it, and uses execution feedback to repair the client until its output matches the data on screen. It then publishes the verified client as an auto-pausing HTTPS API, with optional Stripe test-mode billing.

Built for the CreateOS x Stripe Projects hackathon **Build Agents That Run Code** (October 17, 2026).

## How it works

1. **Explore.** The agent drives Chrome while you watch through a live noVNC view. Network access is restricted to the target site and explicitly allowed backend hosts.
2. **Discover.** A Chrome DevTools Protocol recorder captures JSON requests. The agent identifies the endpoint containing the data shown on screen.
3. **Code, run, fix.** The agent writes `client.py`, executes it in a sandbox, reads errors, and retries within a bounded attempt limit.
4. **Publish.** The verified sandbox is paused and forked into an API sandbox when the plan supports it. On single-sandbox plans, publishing can reuse the source sandbox instead.
5. **Sleep and wake.** The API auto-pauses when idle and resumes on the next request. Optional Stripe test-mode Checkout and usage billing provide prepaid API calls.

## Current status

Unclick is a work in progress. The following paths have been exercised:

- Scripted web demo and an offline pipeline with fake sandboxes.
- Live CreateOS command execution, files, public HTTPS ingress, and pause/resume.
- Desktop Chrome, CDP recording, page text, and screenshots.
- HTTPS API publishing, API-key validation, auto-pause, and wake-on-request.

Full live agent runs are still model- and website-dependent. Free OpenRouter models have returned provider errors or invalid structured output, and the leaderboard example did not trigger its data request within the exploration time limit. Stripe billing tests are deferred; billing is optional for the demo.

The discovery flow currently depends on capturing JSON requests containing the on-screen data. Server-rendered pages may require an interaction that triggers a data fetch, and some websites will not work with this approach.

## Stack

- **CreateOS Sandbox:** desktop and devbox microVMs, egress allowlists, public ingress, fork, and pause/resume.
- **Stripe Projects:** provider provisioning and credential management for CreateOS and OpenRouter.
- **Agent:** OpenAI-compatible Python SDK with a model supporting vision and tool calling.
- **Optional payments:** Stripe test-mode Checkout and usage billing.
- **App:** Python 3.12, FastAPI, server-sent events, vanilla JavaScript, and Tailwind.

## Run locally

Prerequisites: Python 3.12 and Git. The web frontend does not require a Node build step.

### Windows (PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Start the scripted web demo without cloud credentials.
$env:UNCLICK_FAKE = '1'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
UNCLICK_FAKE=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Open **http://localhost:8080**. The scripted demo simulates exploration, repair, publishing, and purchases; it does not create cloud resources or process payments. Stop the server with `Ctrl+C`.

### Live pipeline

Configure these variables in `.env` before starting a live run:

| Variable | Purpose |
| --- | --- |
| `CREATEOS_SANDBOX_API_KEY` or `CREATEOS_API_KEY` | CreateOS Sandbox API access |
| `CREATEOS_SANDBOX_BASE_URL` | Control-plane URL matching your key's region |
| `OPENAI_API_KEY` | OpenAI key, or an OpenRouter key when using its compatible endpoint |
| `LLM_MODEL` | Model ID with vision and tool-call support |
| `LLM_BASE_URL` | Leave blank for OpenAI; use `https://openrouter.ai/api/v1` for OpenRouter |
| `PUBLIC_BASE_URL` | Orchestrator URL; locally, `http://localhost:8080` |

If Stripe Projects supplies credentials under different variable names, map them to the names above for this app. Never commit credentials.

On Windows, restart with live mode selected:

```powershell
$env:UNCLICK_FAKE = '0'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

On macOS / Linux, use the same launch command with `UNCLICK_FAKE=0`.

Keep `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` blank while billing is disabled. The `sk_test_` and `pk_test_` values in `.env.example` are placeholders, not usable credentials. No Stripe payment authentication is needed to run the scripted demo or an unmetered published API.

For a single-sandbox CreateOS plan, set `UNCLICK_FORK=0` to use the source sandbox for publishing. Forking needs capacity for both the paused source and its child. Live runs create cloud resources; destroy published sandboxes when finished.

## Development and checks

The existing smoke scripts cover individual live components:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_sandbox.py --no-fork
.\.venv\Scripts\python.exe scripts\smoke_desktop.py https://example.com
.\.venv\Scripts\python.exe scripts\smoke_publish.py
```

These require CreateOS credentials and create temporary cloud sandboxes. The publishing smoke test supports an unmetered demo when Stripe is unconfigured. Avoid `--keep` if you want its API sandbox cleaned up afterward.

## Project guide

- `app/`: FastAPI orchestrator, event streaming, and web UI.
- `unclick/`: agent, sandbox client, browser recorder, pipeline, publishing, and billing.
- `scripts/`: command-line runner and component smoke tests.
- [Architecture and build plan](docs/PLAN.md)
- [Stripe Projects setup](docs/stripe-projects.md)
- [CreateOS developer reference](docs/createos-sandbox.md)

The provider guides contain dated research notes; check the installed CLI's help for current flags. Install provider CLIs separately if you need to manage services from your terminal. Keep `.env`, credential vaults, and machine-local project state out of version control.
