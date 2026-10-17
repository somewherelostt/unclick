# Unclick

**Unclick any website into an API.**

Half the web has no API. Unclick watches an AI agent do your task once in a real Chrome browser, running inside a [CreateOS](https://createos.sh) desktop sandbox. Along the way it captures the site's hidden JSON calls. Then it writes a Python client, runs it, and fixes it until the output matches what was on screen. Finally it publishes the client as a paid, auto-pausing HTTPS API.

Built for the CreateOS x Stripe Projects hackathon "Build Agents That Run Code" (Oct 17, 2026).

> Work in progress. See [docs/PLAN.md](docs/PLAN.md) for the architecture and build plan.

## How it works
1. **Explore.** The agent drives Chrome in a CreateOS `desktop:1` sandbox, and you watch live over noVNC. Egress is locked to the target site.
2. **Discover.** A CDP recorder captures the site's JSON calls, and the agent picks the endpoint behind the data. When it finds the backend host, that host is added to the egress allowlist live.
3. **Code, run, fix.** The agent writes `client.py`, runs it in the sandbox, reads the errors, and retries until the output matches the screen.
4. **Publish.** The verified sandbox is paused and **forked** into an API sandbox with a tighter egress policy and a public ingress URL.
5. **Bill and sleep.** Each call is charged in Stripe test mode. The API auto-pauses when idle and wakes on the next request.

## Tech
- **CreateOS Sandbox**: desktop + devbox microVMs, fork, egress allowlists, ingress URLs, pause/resume with wake-on-request
- **Stripe Projects**: stack provisioning (CreateOS, OpenRouter). **Stripe** test-mode Checkout + usage billing
- **OpenAI** for the agent (tool calling + vision)
- Python 3.12, FastAPI, vanilla JS + Tailwind

## Run locally
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys, and never commit .env
uvicorn app.main:app --port 8080
```
