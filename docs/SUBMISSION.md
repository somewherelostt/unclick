# Unclick: submission pack

Copy-paste drafts for the submission form, the public post and the leaderboard. Fill in everything marked `TODO`. **Submissions lock 5:30 PM PT (6:00 AM IST).** Missing the video, the public repo, a person on camera or the public post means the project is not judged.

## Final checks before you submit

- [ ] Repo is **public**: https://github.com/somewherelostt/unclick. It has a README, no `.env`, and no `.projects/vault/`.
- [ ] Demo video link works in a private window (see [DEMO_SCRIPT.md](DEMO_SCRIPT.md)).
- [ ] The app has a **live URL** (the leaderboard asks for a deployed URL). TODO: deployed URL.
- [ ] Public post is live and its link is saved.
- [ ] Leaderboard entry is submitted (steps below).
- [ ] No commits after the lock.

---

## 1. Form answers (draft)

**Project name**
Unclick

**One-line description**
Unclick any website into an API: an agent does a task once in Chrome inside a CreateOS sandbox, captures the site's hidden JSON API, writes and fixes a Python client until its output matches the screen, and publishes it as a paid, auto-pausing API.

**Short description (about 150 words)**
Half the web has no API. Unclick turns a URL and a plain-English task into one. An AI agent does the task once, live, in a real Chrome browser running in a CreateOS `desktop:1` sandbox (you watch it over noVNC), while a recorder captures the page's hidden JSON calls. The agent picks the endpoint that holds the on-screen data, then writes a Python client, runs it in a second sandbox, reads the errors and the diff against what Chrome showed, and fixes the code until the output matches the screen. The verified client is published as an HTTPS API on the sandbox's public URL. Each call is billed with Stripe test-mode credits and Billing Meter events. The sandbox's egress is locked to only the backend and Stripe, and it auto-pauses when idle and wakes on the next request. Demo target: the public projects.dev leaderboard.

**What problem does it solve?**
Most useful data lives on pages with no API. Scraping HTML breaks when markup changes, while the hidden JSON API behind the page is stable but undocumented. Unclick finds it, writes a verified client and ships it as a billable API in minutes.

**How does the agent write and run code?**
The pipeline gives the agent the screen, the page text and the captured JSON calls. The agent writes `client.py`, which runs via `exec` in a locked-down `devbox:1` sandbox. The pipeline compares the stdout with the rows read off the screen, and sends back the stderr or the diff when it doesn't match. The agent then fixes the code, up to 5 attempts. It also rejects clients that hard-code on-screen values.

**Tech used**
- CreateOS Sandbox: `desktop:1` + noVNC live view, `devbox:1` exec, egress allowlists, fork (when the plan allows it; falls back to a single sandbox on Free), ingress public URL, auto-pause with wake-on-request
- Stripe Projects: provisioning the stack (`createos/project`, optional `openrouter/api`), shared on the projects.dev leaderboard
- Stripe (test mode): Checkout, Customers, Prices, Billing Meters
- OpenAI-compatible LLM (tool calling + vision; OpenAI or OpenRouter)
- Python 3.12, FastAPI, SSE, httpx, pydantic, Chrome DevTools Protocol, vanilla JS + Tailwind

**How Stripe Projects and CreateOS Sandbox are used** (for the "how it works" field)
Stripe Projects provisions the CreateOS account and credentials for the stack, and we share the stack with `stripe projects share`. CreateOS Sandbox is the runtime: a desktop sandbox so the agent can use a real browser, an egress allowlist to keep it on the target site, an exec loop for the self-fixing code, fork for a tighter-policy API sandbox, ingress for the public URL, and pause/resume with wake-on-request so the API costs nothing when idle. Stripe test mode charges per API call.

**Links**
- Repo: https://github.com/somewherelostt/unclick
- Demo video: TODO
- Deployed app: TODO
- Public post: TODO
- Leaderboard share link: TODO (from `stripe projects share`)

**Team** (TODO: fill in)
- Team name: TODO
- Members + Discord handles: TODO

**Honest notes** (optional field, or keep for Q&A)
Fork needs a paid CreateOS plan (Beginner or higher); on Free the pipeline falls back to publishing in the single sandbox. The scripted mode (`UNCLICK_FAKE=1`) is simulated and is labelled as such. The demo uses public data only, with a handful of requests, and doesn't bypass any bot protection.

---

## 2. X post (under 280 characters, tags @createosai)

```
Unclick: any website → API. An agent does the task once in Chrome in a @createosai sandbox, captures the hidden JSON API, writes + fixes a Python client, and publishes it as a paid, auto-pausing API. Built with Stripe Projects.

https://github.com/somewherelostt/unclick
https://youtu.be/TODO
```

That is 276 characters with each URL counted as 23, as X does. Replace the video link and recount. Attach the video, or a short clip, to the post. Optional hashtags if there is room: `#BuildAgentsThatRunCode`.

## 3. LinkedIn post (tags CreateOS)

Type `@CreateOS` and pick the company page from the dropdown so the tag is a real mention.

```
Half the web has no API. So we built one that makes them.

Unclick turns any website into an API.

You give it a URL and a task. An AI agent does the task once, live, in Chrome inside a @CreateOS desktop sandbox while a recorder captures the page's hidden JSON calls. Then it:

• writes a Python client for the endpoint it found
• runs it in a sandbox, reads the errors and the diff against what was on screen, and fixes its own code until the output matches
• publishes it as an HTTPS API on the sandbox's public URL, locked down to only the backend and Stripe

Every call is billed in Stripe test mode. When nobody is calling, the sandbox pauses itself, and the next request wakes it up.

CreateOS Sandbox does the heavy lifting (desktop + live view, egress allowlists, exec, fork, ingress, pause and wake-on-request), and Stripe Projects provisions the stack.

Built for the CreateOS x Stripe Projects hackathon, "Build Agents That Run Code".

Demo video: TODO
Code: https://github.com/somewherelostt/unclick

#BuildAgentsThatRunCode #StripeProjects #AIAgents
```

---

## 4. projects.dev leaderboard

The app must be deployed and the repo public before you submit.

1. Make sure the Stack is provisioned through Stripe Projects in this folder (`stripe projects status` lists `createos/project`, and `openrouter/api` if you added it). If `.projects/state.json` exists, commit it. Never commit `.env` or `.projects/vault/`.
2. Get your Stripe account ID (the form needs it):
   ```bash
   stripe whoami
   ```
3. Create the share link (it encodes the services only, no secrets):
   ```bash
   stripe projects share
   ```
   Copy the `https://projects.dev/s/...` URL.
4. Go to https://projects.dev/leaderboard and click **Submit Your Project**. You may need to sign in.
5. Paste the **share link**, the **public repo URL** (`https://github.com/somewherelostt/unclick`) and the **deployed URL**.
6. Hackathon code: **`CreateOS-2026`** (case-sensitive, type it exactly).
7. Check that the entry shows up on the leaderboard with the CreateOS filter, and save the entry link.

Ranking on the leaderboard is by community upvotes. Share the X/LinkedIn post to get votes.

## 5. Order of operations (last hour)

1. Final README and repo check, push (before 5:30 PM PT).
2. Record and upload the video.
3. Publish the X and LinkedIn posts (with the video and repo link).
4. Leaderboard submission (`stripe projects share` → projects.dev/leaderboard → code `CreateOS-2026`).
5. Submission form (link posted in Discord #announcements): paste the answers above and the links.
6. Be online at 6:00 PM PT for the live demos if you make the top 5.
