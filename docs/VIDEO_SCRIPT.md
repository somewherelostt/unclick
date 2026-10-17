# Unclick: 3-minute demo video script (Ananya on facecam)

Setup: facecam in a corner the whole time. Screen: the Unclick app (http://localhost:8082, **LIVE** badge). Start the run at 0:55 and keep talking while it works, about 70 s. If it is slow, cut or speed up the waiting in the edit.

---

### 0:00 to 0:15. Hook (full-screen facecam, or facecam over the landing page)
**Say:** "Hi, I'm Ananya, and this is **Unclick**. You give it any website, and it gives you back a real API in about a minute, built by an AI agent that writes and runs its own code."

### 0:15 to 0:45. The problem
**Screen:** the landing page, scrolled slowly.
**Say:**
"Half the web has no API. The data is right there on the screen: flight fares, listings, jobs, search results. But if you're a developer, you can't *call* it.
So people do one of three things. They copy-paste by hand. They write scrapers that break every time the page changes. Or they use browser agents that click through the whole site **every single time**, which is slow, flaky, and burns AI tokens on every request.
But most modern sites load their data from a *hidden* JSON API. You just have to find it."

### 0:45 to 0:55. Our solution
**Screen:** the hero ("Unclick any website. Into an API.") and the 3-step strip.
**Say:**
"Unclick finds it for you. An agent does the task **once** in a real browser, finds the hidden API, writes a client, **checks it against what's on screen**, and publishes it as a paid API. After that it's one plain HTTP call, with no browser and no AI."

### 0:55 to 2:15. Live end-to-end run
**Screen:** the input shows `hn.algolia.com/?q=ai` and "Search Hacker News stories: title, points, url". Click **Unclick it**.
**Say, while it runs, one beat per phase:**
- **Boot:** "This spins up a **CreateOS sandbox**, an isolated micro-VM with Chrome inside. You're watching its screen live."
- **Egress card:** "Notice the egress allowlist. The sandbox can **only** reach this one site. Everything else on the internet is blocked."
- **Explore / Discover:** "The agent searches like a human would, while we record every network call. Here it found the real data source. And the allowlist grows by exactly that one host, live."
- **Code / Verify:** "Now it **writes its own Python client**, runs it in a second sandbox, and compares the output with the rows it saw on screen. If it fails, it reads the error and fixes its own code."
- **Publish:** "Verified. It publishes the client on a public CreateOS URL, and sets the sandbox to **auto-pause** when idle, so an unused API costs almost nothing."

### 2:15 to 2:45. The API page
**Screen:** the API page. In **Try it**, change `q` to `stripe` and click **Send**.
**Say:**
"And now it's a real API. The search box became a typed parameter. I'll change it to 'stripe' and send: results in about **a second**, and it charged **one call through Stripe** in test mode.
It also has a typed OpenAPI schema, and an **MCP tab**, so Claude or Cursor can use this as a tool.
In **My APIs**, here are others it built the same way: crates.io search and the projects.dev leaderboard, each with a health check and **self-heal** that re-runs the agent if a site changes."

### 2:45 to 3:00. Sponsor tech and close (facecam)
**Say:**
"Our whole stack was set up with **Stripe Projects**, which provisioned CreateOS for us. **CreateOS sandboxes** run every step: the desktop browser, egress lockdown, running the agent's code, public URLs, and pause and wake. **Stripe** bills every call.
That's Unclick: do it once, and call it forever. Thanks!"

---

## Recording checklist
- The orchestrator frees the CreateOS sandbox slot right before recording. Do **not** start runs before then.
- Browser at 100% zoom, 1920×1080, notifications off.
- If the live run fails, record the same flow with `http://localhost:8082/?replay=demo-real-createos-hn-search` (a real recorded CreateOS run of exactly this HN search, labelled REPLAY) and say "here's a recorded real run".
- Verified on real CreateOS before recording: HN search went live in about 110 s. Egress grew by `uj5wyc0l7x-dsn.algolia.net` (the hidden backend), the client passed on attempt 1, and `?q=stripe` returned in 0.88 s.
- Upload to YouTube (unlisted) or Loom, check the link opens in incognito, and put it in the README and the form.
