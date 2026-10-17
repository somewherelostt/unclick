# Unclick: 2:30 demo video script

**Required by the hackathon:** 2 to 3 minutes, a team member **on camera**, covering problem → how it works → live demo of the agent writing and running code → how Stripe Projects + CreateOS Sandbox are used. A video without a person on camera is not judged.

**Format:** screen recording of the app with a face-cam bubble (bottom right) for the whole video. Show the full face-cam, full screen, for the first 10 s and the last 10 s. Unlisted YouTube, Loom or Drive, with the link viewable by anyone.

**Target runtime: 2:30.** Hard stop 2:55. The live pipeline is slower than 2:30, so record the run once, then **cut or speed-ramp the waiting** (boot, noVNC load) in the edit. Keep the speed-up visible with a "2x" or "cut" label so nothing looks faked. Narrate over the sped-up parts.

Lines in **bold** are said on camera or as voice-over. `[brackets]` are screen actions.

---

## 0:00–0:15 · The problem (face-cam full screen, then slide into the app)

`[Face-cam full screen, then cut to the app's empty home screen]`

> **"Half the web has no API. The data is right there on the screen, but you can't call it, and scraping breaks every time the page changes. Most sites do have a hidden JSON API behind the page, but nobody documents it. Unclick finds it for you. It turns any website into an API."**

## 0:15–0:55 · Live run on projects.dev/leaderboard (explore + discover)

`[URL field shows https://projects.dev/leaderboard, task: "Get the top projects with name, votes and URL". Click "Unclick it". Show the stepper and the desktop panel.]`

> **"I paste a URL and a task. Unclick boots a CreateOS desktop sandbox: a real Chrome in a microVM, and you're watching it live over noVNC."**

`[Live desktop iframe: Chrome opens the leaderboard, the agent clicks the dropdown. Point at the Egress allowlist panel: locked to projects.dev.]`

> **"The sandbox's egress is locked to this one site. An agent can only reach what it needs."**

`[Hidden JSON calls panel fills in; the endpoint box shows GET projects.dev/api/leaderboard]`

> **"While the agent does the task, a recorder captures every JSON call the page makes. This page renders on the server, so the agent changes the filter to make the page fetch its data. And there it is: the hidden API, picked because its response contains exactly what's on screen."**

*Do not say a host was added to the allowlist unless it really happened on the recording. On projects.dev the API is on the same host, so the allowlist doesn't grow. See the "if the egress-add happens" note below.*

## 0:55–1:25 · Write, run, fix (the self-fix loop)

`[Code tab: client.py attempt 1. Runs tab: red exit code / stderr or a failing Verify. Then attempt 2 appears, Verify turns green.]`

> **"Now the agent writes a Python client for that endpoint and runs it in a second sandbox, locked to just the API host. The first run isn't right. The agent reads the error, and the diff against what Chrome showed, fixes its own code and runs again."**

`[Zoom on the attempt pills: 1 red → 2 green. Verify pane: "All N on-screen items found".]`

> **"Attempt two matches the screen, number for number. That's the loop: write code, run it in the sandbox, read the output, fix it."**

*If attempt 1 happens to pass on the take, say so and move on, or re-record. Don't pretend it failed. If you want the fix moment on camera, use a recorded take where it really happened.*

## 1:25–1:50 · Publish: fork, public URL, curl

`[Sandboxes panel: the verified sandbox pauses, then forks. Egress panel shows [projects.dev, api.stripe.com]. The API card appears with the curl.]`

> **"Verified. Now we publish. Unclick pauses the sandbox, forks it into an API sandbox with the same warm state but a tighter network policy: only the backend and Stripe. And it gets a public HTTPS URL."**

*Plan variant: say the fork line only if the recording shows a "forked" event (Beginner plan or higher). On the Free plan, say instead:* **"It publishes the verified sandbox on its own public URL, locked down to only the backend and Stripe."**

`[Terminal or the "Try it" button: run the curl from the API card.]`

```bash
curl -H "X-Unclick-Key: <demo key>" "https://<id>-8000.app.sb.createos.sh/v1/run"
```

> **"One curl, clean JSON, under a second, and no LLM in the request path. The website is now an API."**

## 1:50–2:05 · Stripe charge

`[Billing area in the UI: "Buy 100 calls" → Stripe Checkout in test mode (card 4242 4242 4242 4242) → back in the app with an API key and "calls remaining". Then call the API with the new key; remaining count drops.]`

> **"And it's a product. Buy 100 calls through Stripe Checkout, here in test mode, and you get an API key. Every call uses one credit and sends a Stripe meter event from inside the sandbox. Out of credits, you get a 402."**

*If Stripe Checkout isn't verified on the take, show only the metered call (the "calls remaining" header dropping) and the Stripe Dashboard test-mode meter events, and don't say "Checkout" unless you show it.*

## 2:05–2:20 · Auto-pause and wake

`[Sandboxes panel / API card: status "paused" after about a minute idle. Click "Wake test" or run the curl again. Show the latency number.]`

> **"When nobody's calling it, the sandbox pauses itself, so it costs nothing while idle. The next request wakes it up: about six seconds from cold, and the data comes straight back."**

*Use the latency number the UI actually shows. In our live test it was about 6 s.*

## 2:20–2:30 · How Stripe Projects + CreateOS are used (face-cam back to full screen)

`[Short cut to the README architecture diagram or the Stripe Projects share page, then face-cam]`

> **"We provisioned the stack with Stripe Projects: CreateOS for the sandboxes, and Stripe for billing. CreateOS does the heavy lifting: a desktop sandbox with live view, egress allowlists, an exec loop for self-fixing code, fork, a public URL, and pause with wake-on-request. This is Unclick: unclick any website into an API. The code is on GitHub."**

`[End card: "Unclick · github.com/somewherelostt/unclick"]`

---

## If the egress-add happens (site with the API on another host)

If you record on a target whose API is on a different host (for example a site that uses a search backend), add this to the 0:15–0:55 section and keep it short:

> **"Watch the allowlist: the page tried to reach a backend host we hadn't allowed, so the agent adds exactly that host, live. Nothing else."**

Only say it if the Egress panel shows the `+ host added` chip on the recording.

## Recording checklist

**Before recording (T-60 min)**
- [ ] CreateOS plan is **Beginner** if you want fork on camera. Free allows 1 concurrent sandbox and **10 sandboxes per day**, and one full run uses 2 to 3 sandboxes. Count your runs and keep a spare for the final take.
- [ ] No leftover sandboxes: dashboard or `default_factory().destroy_all()` (a running API sandbox blocks the next run on Free).
- [ ] `.env` has valid CreateOS, LLM and Stripe **test** keys. `STRIPE_SECRET_KEY` starts with `sk_test_`.
- [ ] `PUBLIC_BASE_URL` is the URL you will be recording from (Checkout redirects back to it).
- [ ] `uvicorn app.main:app --port 8080` runs with **no** `UNCLICK_FAKE`. The header must not say FAKE or "NO PIPELINE".
- [ ] Do one **dry run** end to end and note the real timings (boot, attempts, publish, wake).
- [ ] Stripe Dashboard open in test mode, on the Billing meter events page, in another tab.
- [ ] Browser window at 1920x1080, zoom 100%, notifications off, personal tabs and bookmarks hidden, no API keys visible on screen. Don't show `.env`.
- [ ] Face-cam, mic check and good light. Quiet room.
- [ ] Have the scripted fallback (below) running in another tab, ready to go.

**During**
- [ ] Record the whole run in one take, then trim. Keep raw footage of each take.
- [ ] Say what is on screen at each moment. Don't claim anything the screen doesn't show.
- [ ] Use the **test card 4242 4242 4242 4242**, any future date and any CVC.
- [ ] Trigger the auto-pause moment while narrating something else. It takes about 60 s idle, so start the idle timer right after the first curl.

**After**
- [ ] Length is between 2:00 and 3:00.
- [ ] Face is on camera at the start and the end, and in the bubble throughout.
- [ ] Upload (unlisted YouTube, Loom or Drive), open the link in a private window to check it plays.
- [ ] Add the link to the submission form and to the post.
- [ ] Destroy all sandboxes after recording.

## Fallback: scripted demo (`UNCLICK_FAKE=1`)

If live sandboxes fail (quota, a flaky desktop, a slow LLM), record this instead. Don't hide that it's scripted.

```bash
UNCLICK_FAKE=1 uvicorn app.main:app --port 8080
# PowerShell: $env:UNCLICK_FAKE="1"; uvicorn app.main:app --port 8080
```

- The run takes about 75 s at normal speed. `UNCLICK_FAKE_SPEED=4` makes it 4x faster, but use 1x for the video.
- It replays every UI moment: live desktop, egress growing (`+ api.projects.dev`), the first client failing then passing, fork, the API card, a charge, auto-pause and wake.
- **Everything in it is simulated.** The sandboxes, the data (project names and votes) and the Stripe charge are scripted, and the host `api.projects.dev` and the sandbox URL are made up.
- **Say it on camera** at the start of the demo section: **"This is our scripted replay of a run, because live sandboxes were unavailable. The real pipeline is in the repo."** Add an on-screen label such as "scripted replay".
- Best practice: use the fake only for the parts that failed live, and show any real footage you do have (a real run's logs, the real `curl`, the Stripe test dashboard) next to it.
- Same timeline and lines as above. Drop the lines that claim live features you can't show (real fork, the real Checkout).
