# Reference insights: what to borrow, what to beat

Internal doc. "The reference product" is the commercial website-to-API service we studied (public site, docs, comparison pages, a catalog detail page, and the signed-in app). We take inspiration only. Nothing below is copied text, and the reference product must not be named in anything judges see: the video, the README, the post, the submission form.

**What I could and couldn't see.** I read every public page (landing, pricing, FAQ, features, industries, comparisons, changelog, `llms.txt`, catalog, a full catalog detail page), the whole docs site (intro, quickstart, dashboard guide, marketplace, updates, errors), and the signed-in Settings page. The signed-in app opens behind a four-step onboarding modal. Dismissing it would write an account flag, so I left it. I saw onboarding steps 1 and 2 (pick a category, then a live API playground) and read the New API, Discover, API-detail and snippet screens through the docs' own description. I did not see the build-progress screen or the billing page (`/billing` is a 404; credits are a header button). Screenshots from the session were saved to the tool-results folder, not the repo.

---

## 0. What the reference product is, in 10 lines

- Pitch: "an API for the entire internet". The pitch is *speed (100x, no browser)* and *one typed call instead of a hundred clicks*. It talks straight to the network layer, so it works only where a site has such calls.
- Users paste a URL plus a sentence. An agent builds typed REST endpoints plus an MCP server in about 60 s. Builds that fail halfway keep the endpoints that already passed.
- **Marketplace / canonical model:** one shared, community-maintained API per site. Subscribe (a pinned copy), fork privately, or call the canonical directly. About 4,000 listings, and the landing page leads with that count.
- **Reliability story:** scheduled health probes, auto-repair when a probe fails, re-verify, and a visible "verified Nh ago" stamp. Each endpoint row shows "N/N passing latest check".
- **API detail page:** an endpoint list on the left with a per-call credit price, typed params, a "Send request" playground and a live response pane. Below that a natural-language "tell the agent what to change" bar with suggestion chips. Then a snippet box with tabs (cURL, Python, TypeScript, MCP, OpenAPI), "replace `$KEY`" and "Get your API key".
- Docs are agent-friendly: an `llms.txt`, a hosted MCP server with create/list/get/revise/call tools, an OpenAPI export, a typed Python SDK, and an error envelope that says whose fault a failure is (422 yours, 502 the site's, 503 bot protection, 500 the scraper's).
- Pricing: a free tier (200 credits, no card), then $30 / $100 / $300 / $1,000 a month. Credits are per call and vary by endpoint (1 to 5 on the page I read). They are "charged only on success" and returned in response headers. Private builds cost 75 credits and revisions 50.
- Proof elements: the live-API count, a "LIVE" tag, "verified 3h ago", "10/10 passing", "backed by YC", honest "when to choose them / when to choose a browser" comparison pages, per-industry pages, and a dated changelog.
- Weak spots we can use: no visible proof the data matches what a person sees on screen; no visible isolation story; closed source; the cost model is opaque to the caller until they read headers; the built code runs on their infrastructure, so you can't inspect or take it.

---

## 1. Demo video

### What their story and visuals do well
1. **One before/after, always.** A browser agent clicking through a booking form, a clock and a dollar meter climbing, against one 0.4 s call with clean JSON. The viewer gets the thesis in 5 seconds without reading anything.
2. **Numbers on screen** (a latency figure, a status code, a price) in a monospace "terminal" look. It feels real.
3. **The result is shown in three consumers:** HTTP, then an MCP tool in an AI client. It lands as "this is infrastructure, not a demo".
4. **A trust section ("fixes itself") as a four-step loop:** monitor, detect, repair, verify, with a visible last-verified stamp.
5. **Copy is short and lowercase-confident:** "URL in, API out", "no SDK, no config, no YAML".

### What we should do differently
Their video story is an *explanation*. Ours has something they can't show: **a watchable agent that writes code, fails, and fixes itself against the screen**, inside a real desktop sandbox. Spend the screen time on that.

Rules for our cut:
- Face-cam bubble throughout; full-screen face for the first 8 s and last 10 s (required).
- Only claim what the screen shows. The flights example is the *motivating problem* (b-roll / one slide), not something we run live unless we really recorded it. The live run is on projects.dev.
- Every time-skip gets a visible "2x" or "cut" chip.
- Put a running **timer chip** (top-left) that starts at the click on "Unclick it" and stops at "API live". On the real run this is about 68 s. The viewer sees the number, which is stronger than saying it.

### Revised 2:30 beat sheet

Times are targets. "Show" lists the exact on-screen element. Captions are short white-on-dark lower-thirds.

| # | Time | Beat | Say (voice-over / on camera) | Show | Caption |
|---|---|---|---|---|---|
| 1 | 0:00–0:12 | **Hook** (face-cam full screen, then slide into app) | "I wanted flight prices from a site that has no API. The data is on the screen, but I can't call it." | Face-cam, then a 3-second slide: a flight-search page, a red "no API" label. Then cut to the empty home screen. | "Half the web has no API." |
| 2 | 0:12–0:24 | **Problem** | "Scrapers break the moment a page changes. But behind most pages there's a hidden JSON API nobody documents. Unclick finds it, writes the client, and proves it works." | Quick split: HTML soup on the left, a JSON response on the right. | "Scrape the screen → or find the API behind it" |
| 3 | 0:24–0:50 | **Live agent run** (discover) | "I paste a URL and a task. Unclick boots a real Chrome in a CreateOS desktop sandbox. You're watching it live." | **CreateOS: desktop live view** (noVNC iframe) with Chrome loading projects.dev/leaderboard; timer chip starts; stepper highlights "Explore". Point at the **Egress allowlist** panel locked to one host. If a second host really gets added on the recording, point at the `+ host added` chip; otherwise do not say it. Hidden-JSON-calls panel fills; the endpoint box locks onto the chosen call. | "Real Chrome. Live view. Network locked to 1 site." |
| 4 | 0:50–1:15 | **Write, run, fix** (the self-fix moment) | "Now the agent writes a Python client and runs it in a second sandbox. Attempt one is wrong. It reads the error and the diff against what Chrome showed, fixes its own code, and runs again." | Code tab (client.py v1) → Runs tab with a red result → v2 with a diff highlight → Verify pane turns green: "All N on-screen rows found". Zoom the attempt pills `1 red → 2 green`. | "Write → run → read the error → fix" and then "Matches the screen: N of N rows" |
| 5 | 1:15–1:32 | **Publish** (CreateOS fork + live URL) | "Verified. Unclick pauses that sandbox and forks it into an API sandbox with the same warm state and a tighter network policy: just the backend and Stripe. It gets a public HTTPS URL." | **CreateOS: pause, fork, live URL.** Sandboxes panel (verified sandbox pauses, fork appears), egress chips for the API sandbox `[projects.dev, api.stripe.com]`, then the API card with its URL. Say the fork line only if a "forked" event is on screen (Free plan: say "publishes it on its own public URL"). | "Pause → fork → public URL" |
| 6 | 1:32–1:42 | **curl at about 1.3 s** | "One curl. Clean JSON in about a second, and no LLM in the request path." | Terminal or "Try it" button; show `-w "%{time_total}"` so the number is real. Response with typed fields. Also flash the schema chips. | "200 OK · 1.3 s · no LLM at request time" |
| 7 | 1:42–1:55 | **MCP tool call from an AI client** | "The same API is an MCP tool. Paste this config into Claude and ask a question." | The MCP tab of the API card (config already filled in), then a Claude/Cursor window calling `get_<name>` and answering from the live data. Keep to one question, one answer. | "Same API → MCP tool for any agent" |
| 8 | 1:55–2:08 | **Stripe charge** | "And it's a product. Buy 100 calls with Stripe Checkout, in test mode, and you get a key. Each call is one credit and a meter event from inside the sandbox. Out of credits, it returns a 402." | Billing area → Stripe Checkout (4242 card) → back with key and "calls remaining"; call again and the number drops; 1-second cut to the Stripe Dashboard meter events. If Checkout isn't verified on the take, show only the metered call plus the Dashboard and don't say "Checkout". | "Stripe test mode · metered per call" |
| 9 | 2:08–2:18 | **Auto-pause / wake** | "When nobody's calling, the sandbox pauses itself and costs nothing. The next request wakes it in about six seconds." | API card status flips to "paused", then a curl, with the real latency number the UI shows. | "Idle = $0 · wake on request ≈ 6 s" |
| 10 | 2:18–2:30 | **Sponsor-tech recap** (face-cam full screen) | "We provisioned the stack with Stripe Projects. CreateOS does the heavy lifting: desktop sandbox with live view, egress allowlists, an exec loop that fixes its own code, fork, a public URL, and pause with wake-on-request. This is Unclick: unclick any website into an API. It's open source." | Face-cam over a lower-third list of 5 sponsor features with ticks; end card with the repo URL. | "Stripe Projects · CreateOS Sandboxes · open source" |

**Exact moments to show the sponsor tech (so judges can tick them off):**
- *CreateOS desktop live view*: beat 3, the first 5 s of the run, full-width.
- *Egress allowlist growing / locked*: beat 3 (locked to the target), beat 5 (swapped to backend + Stripe). Hold each for 2 s.
- *Fork / pause / resume*: beat 5 (pause + fork), beat 9 (pause + wake).
- *Live URL*: beat 5, then beat 6 (the curl hits it).
- *Stripe*: beat 8 (Checkout, meter events); beat 10 (Stripe Projects, named out loud).

**Cutting guidance:** the pipeline is slower than 2:30. Record once, speed-ramp the boot and noVNC load, and keep the 68 s timer chip honest by showing the real elapsed time at the end ("68 s real time, sped up here").

---

## 2. Landing / hero for our UI

Their order: promise (a big number) → vs. the old way → how it works in 3 steps → capabilities → "why it's fast" → reliability loop → marketplace → pricing → FAQ → final CTA. The hero shows a live count plus quick-try chips for popular sites. Their weakness: lots of claims, nothing showing *proof that the output is right*. That's our opening.

### Hierarchy (our words)
- **Eyebrow (small):** `Open source · runs in CreateOS sandboxes`
- **Headline:** `Unclick any website into an API.`
- **Subhead (one sentence, concrete):** `Paste a URL and say what you want. An agent drives a real Chrome, finds the hidden JSON behind the page, writes a client, and fixes it until it matches what's on screen.`
- **Primary CTA:** `Unclick a site →` (opens the run form with a prefilled projects.dev example).
- **Secondary CTA:** `Watch a real run (68 s)` (links to the recorded replay mode, already built).
- **Under the CTAs, a one-line input** (URL + task) with example chips: `projects.dev leaderboard`, plus two more that we have actually verified. Don't add a chip we haven't run.

### Proof strip (directly under the hero)
Four monospace stat tiles, all of them real numbers from our runs:
- `68 s` URL to live API (real run)
- `1.3 s` first call, no LLM in the path
- `N / N` on-screen rows matched
- `6 s` wake from paused, `$0` idle

Plus two badges used elsewhere on the page: a green `verified live · 2m ago` badge (clock updates) and a small `checked against the screen` badge with a tooltip: "every field is compared with what Chrome rendered."

### Section order
1. Hero + proof strip.
2. **Before / after** (side by side): "Scrape the page" (fragile CSS selectors, red cross) vs. "Call the API behind it" (one request, typed JSON, green tick).
3. **How it works, 4 steps:** Explore (live desktop) → Discover (the hidden call) → Write and fix (loop) → Publish (URL, key, billing).
4. **See it work** (the replay player or three screenshots: live view, attempt pills, API card).
5. **What you get:** typed schema, OpenAPI, MCP endpoint, health check, Stripe-metered keys, auto-pause.
6. **Why it's safe:** sandbox per run, network locked to the target host, nothing stored in the request path. (Their pages don't have this; ours should.)
7. **Catalog** of published APIs, with health badges (even if it holds only 3 to 5 real entries).
8. **Under the hood** (for the sponsors): one tidy row: CreateOS (live view, egress, fork, pause/resume, live URL) + Stripe Projects + Stripe metering.
9. **FAQ, 5 questions** (see below). 10. Final CTA + repo link.

### Microcopy patterns that worked for them (write ours in the same spirit)
- Verb + object + result, no filler: "URL in, API out." → ours: `URL in. Verified API out.`
- Negation lists as feature chips: "No SDK, no config, no YAML" → ours: `No scraper to maintain · no LLM per request · no always-on server`.
- Honest "choose X when… / choose Y when…" boxes. → ours: `Use Unclick when a site has no API and you need data repeatedly. Use a browser agent when the task is open-ended or visual.`
- Status words, not paragraphs: `live`, `verified 3h ago`, `passing`, `paused`.
- Show the price at the point of use: `1 call = 1 credit` next to the Run button.
- FAQ answers that start with "Yes." / "No." / the number.

FAQ list for us: *Will it work on every site?* (No: sites whose data is only in server-rendered HTML get discovered by triggering a fetch; we say when a site isn't a fit.) *Is my data safe?* (Isolated sandbox per run.) *What happens when the site changes?* (Health check, then a heal run.) *What does a call cost?* *Can I take the code with me?* (Yes, it's plain Python and open source.)

---

## 3. API detail page / product UX: ranked list

Ranked by judge impact ÷ effort. "Backend exists" refers to the endpoints in `side-session-notes/product-features-api.md` (OpenAPI, `/schema.json`, `/mcp`, `/health/verify`, `/api/catalog`, heal/verify routes). All items touch `app/static/*` only.

| Rank | Idea | Why judges care | Effort | Notes |
|---|---|---|---|---|
| 1 | **Health badge + "verified Ns ago"** on the API card, with a Re-verify button | It's the single most "this is real infrastructure" element, and it shows the sandbox actually being used again after the run. | ~10 min | Backend exists: `GET {base}/health/verify` and `POST /api/apis/{id}/verify`. Show `● healthy · verified 14 s ago · 52 rows` and a red state with the first `problem`. |
| 2 | **Code tabs: curl · Python · JavaScript · MCP** with a Copy button | Gets the "call it from anywhere" beat to be one click and screen-recordable. The MCP tab sets up beat 7 of the video. | ~15 min | Build strings client-side from `api_url`, `mcp_config`, and the demo key. Fill the key in, as the reference does. |
| 3 | **Typed schema chips** under the title (`name: string`, `votes: integer`, `url: string\|null`) | Instantly communicates "typed, not scraped". Cheap and visual. | ~10 min | Publish event already carries `fields` (name, type, required). Link a small "OpenAPI" and "JSON Schema" pill to `openapi_url` and `{base}/schema.json`. |
| 4 | **Per-call price line** next to Run: `1 call = 1 credit · 100 calls = $X` and live `calls remaining` | Ties Stripe to the product at the point of use. They put the price at the endpoint level, and it works. | ~5 min | We already return `calls_remaining` in `meta`; read it after each call. |
| 5 | **Playground form from the client params** (inputs for each `run()` param, "Send request", JSON response pane with latency) | Their detail page leads with this. It lets a judge *use* the API without a terminal. | ~20 min | We have a "Try it" button; extend it to render inputs from `openapi.json` query params and show `meta.ms`. |
| 6 | **"Matched the screen" evidence card**: left = screenshot or on-screen rows, right = API rows, N of N ticks | This is our moat and nobody else shows it. Cheap if we already keep the Verify result. | ~15 min | Reuse the Verify pane data from the pipeline. |
| 7 | **Catalog page** (`/catalog`): cards with name, source host, endpoint count, health dot, price | Gives the "this is a platform" impression, and the landing page can show a count. | ~20 min | `GET /api/catalog` exists. Even 3 real entries look credible. Don't invent entries. |
| 8 | **Error envelope with "whose fault"** (`402 out of credits`, `401 key`, `502 site changed → heal available`) and a "Heal" button | The reference's docs make failures self-explanatory, which is great for agents. It also feeds the self-heal story. | ~15 min | Backend has `POST /api/apis/{id}/heal`. Surface its status inline. |
| 9 | **"Tell the agent what to change" bar** with 3 chips (`Add a field`, `Add a filter`, `What can this do?`) on the API card | It makes "agent keeps working after publish" visible. | ~15 min UI only (the action can be a stub that calls heal with a note) | Be honest if it is only heal. Don't fake a revise run. |
| 10 | **`/llms.txt` on each published API** (one paragraph, the tool name, the key header, the MCP URL) | Agent-native touch; costs almost nothing. | ~10 min in `unclick/api_template/server.py` | Backend change, not UI; only if the web UI session is done. |

If time allows only three: **1, 2, 3** together (about 35 min), then 4.

---

## 4. Positioning lines (judge-safe, no product names)

Pick one for the X/LinkedIn lead and one for the submission description.

1. **"Half the web has no API. Unclick finds the one hiding behind the page, writes the client, and proves it matches the screen."**
2. **"Not a scraper. An agent that writes code, runs it in a sandbox, and keeps fixing it until the output equals what Chrome shows. Then it ships it as a paid API."**
3. **"URL in. Verified, billable API out. 68 seconds, no LLM in the request path, and it sleeps at $0 until someone calls."**
4. **"Every site becomes a tool for your agent: REST, OpenAPI and MCP from one paste. Built on CreateOS sandboxes, sold through Stripe."**
5. **"Browser agents are slow and unrepeatable. We use the browser once, to learn the site, then replace it with a 1.3 s typed API call."**

Submission-description skeleton (3 sentences): problem (no API, scrapers break) → what the agent does (drives Chrome in a CreateOS desktop sandbox, captures the hidden JSON call, writes and fixes a client until it matches the screen) → what ships (a paid, auto-pausing API with MCP and a typed schema, provisioned with Stripe Projects).

---

## 5. Gaps we beat them on (and how to make each one visible)

| Gap | Our advantage | Make it visible |
|---|---|---|
| **Isolation per run** | Every run gets its own microVM sandbox, and the published API runs in a forked sandbox, not a shared service. | Sandboxes panel with one row per run; caption "one sandbox per run" in beats 3 and 5. Their pages describe a hosted shared service. |
| **Egress lockdown** | The agent's network is allowlisted to the target host, then tightened to backend + Stripe at publish. A mis-prompted agent can't reach anything else. | Egress chips on screen; a line in the README "why it's safe". Their material talks about proxies and anti-bot, not about constraining the agent. |
| **Verified against the screen** | The client isn't accepted until its output matches what Chrome rendered, number for number. | "N of N rows matched" card (item 6 above) and the attempt pills. Their proof is a probe's pass/fail, ours is a diff against reality. |
| **Self-fix is visible and inspectable** | You can watch attempt 1 fail and attempt 2 pass, and read the diff. | Beat 4 of the video. Their repair loop is described in words only. |
| **Wake-on-request cost** | Idle APIs are paused sandboxes that cost nothing; the next call wakes them (about 6 s). | Beat 9, plus "Idle = $0" on the price line. They charge a monthly subscription plus credits. |
| **Open source, portable code** | The generated client is plain Python you can read, run and take with you; the whole stack is on GitHub. | "Open source" in the headline eyebrow and the end card; a `Download client.py` link on the API card (cheap). |
| **Pay-per-call from the first call** | Stripe-metered keys with a 402 on empty, test mode today. | Beat 8. |

**Be honest about what they do better** (judges will respect this, and it keeps our claims safe): they have thousands of prebuilt APIs and a shared repair model, authenticated sites, captcha/proxy handling, and a typed SDK. We should not claim breadth. Our pitch is depth: *a verified, isolated, inspectable build you can watch*. A good one-liner for the FAQ: "We are not a catalog. We are the part that builds and proves one."

---

## 6. Top 5 actions in the time left

1. Make the video's centre of gravity the **fail → fix → "N of N rows match"** moment, and add the **timer chip** (beat 4 and the whole run).
2. Ship **health badge + last-verified + schema chips + code tabs (with MCP)** on the API card (items 1 to 3 plus 2, about 35 min).
3. Add the **`1 call = 1 credit` price line and live calls remaining** next to Run (item 4, 5 min), then film beat 8 off it.
4. Put a **proof strip** under the hero using only real numbers (68 s, 1.3 s, N/N rows, 6 s wake) and the headline `Unclick any website into an API.`
5. Lead the post with **line 1 or 2**, and add an honest "when to use a browser agent instead" note to the README FAQ. It reads as confident, not defensive.
