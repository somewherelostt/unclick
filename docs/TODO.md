# Hackathon TODO (CreateOS x Stripe Projects)

Times are PT, with IST in brackets. **Submissions lock 5:30 PM PT (6:00 AM IST).**

## 1. Accounts and access (now, everyone on the team)
- [ ] Luma approval confirmed for the whole team (one person registers, teammates added in the form)
- [ ] Join the hackathon Discord (the invite from the Luma confirmation). Turn on notifications for #announcements, since the submission form link drops there before 5:30 PM PT
- [ ] Create a CreateOS account at https://createos.sh/app and **generate an API key** at https://createos.sh/app/profile (credits are only transferred after you do)
- [ ] Have a Stripe account (free, no need to activate payments, works outside the US)

## 2. Stripe Projects setup (mandatory, one machine)
- [x] Stripe CLI installed (v1.54.0)
- [ ] `stripe login`. If it fails: Dashboard, enable **"MCP and CLI access"** (needed on CLI > 1.50)
- [ ] `stripe plugin install projects` (or `stripe plugin upgrade projects`)
- [ ] In this folder: `stripe projects init <project-name>`. Backup if it fails: https://dashboard.stripe.com/projects/new
- [ ] `stripe projects add createos/project`. This provisions CreateOS through Stripe Projects and writes creds to `.env`
- [ ] `stripe projects add openrouter/api`. This is the only LLM provider in Projects (OpenAI-compatible API, so the OpenAI SDK works with OpenRouter's base URL). Keep your own OpenAI key as a backup
- [ ] `stripe projects env`: check that `CREATEOS_SANDBOX_API_KEY` exists. If it doesn't, put the key from the CreateOS dashboard into `.env`

## 3. CreateOS Sandbox smoke test
- [ ] Install the CreateOS CLI: https://github.com/NodeOps-app/createos-cli (the install script is bash, so run it in Git Bash on Windows)
- [ ] `createos login` (if asked)
- [ ] `createos sandbox create --shape s-1vcpu-1gb --name agent-1`
- [ ] `createos sandbox exec agent-1 -- python3 -c 'print("hello")'`
- [ ] Note: the **free plan = 1 concurrent sandbox, 10/day, max 1 vCPU / 1 GB**. Fork needs a paused parent plus a child (2 sandboxes), and desktop wants 4 vCPU, so **both will likely 429 on Free**. The Beginner plan allows 5 concurrent: `stripe projects update createos-plan --service beginner-monthly`. Ask in Discord whether hackathon credits cover it (new accounts get 500 credits, billed per second)
- [ ] Destroy sandboxes when you're done. There's no max lifetime, so idle boxes keep billing

Features judges reward: **fork** (a paused sandbox gets copied, memory and disk included), **egress allowlist**, **ingress public HTTPS URL**, **pause/resume + wake-on-request URL**, **`desktop:1` image** (Chrome + computer use), and the `devbox:1` image (Python, pandas, Docker, coding agents).

## 4. Decide (by ~12:45 PM PT / 1:15 AM IST)
- [ ] Pick the idea (see `docs/ideas.md`)
- [ ] Pick the stack
- [ ] Split the work across teammates and sessions

## 5. Build (12:30 to ~4:30 PM PT / 1:00 to 5:00 AM IST)
- [ ] The agent writes code, runs it in the sandbox, reads the errors, and fixes itself (the core judging criterion)
- [ ] Use at least 2–3 sandbox features for a real reason
- [ ] Use Stripe for something real (Checkout, Payment Link, or metered billing) if it fits
- [ ] **Deploy the app.** The leaderboard needs a live URL
- [ ] `.gitignore`: `.env`, `.projects/vault/`. Never commit keys

## 6. Submit (4:30–5:30 PM PT / 5:00–6:00 AM IST)
- [ ] **Public GitHub repo** + README (what it does, how to run it, tech used)
- [ ] **Demo video, 2–3 min, a team member on camera**: problem, how it works, a live demo of the agent writing and running code, how Stripe Projects + CreateOS Sandbox are used. YouTube unlisted, Loom, or Drive, with the link viewable
- [ ] **Public post** on X (tag @createosai) or LinkedIn (tag CreateOS)
- [ ] **Project details**: team name, members + Discord handles, project name, short description
- [ ] **Submission form** (link posted in #announcements before 5:30 PM PT)
- [ ] **projects.dev leaderboard**: `stripe projects share`, then https://projects.dev/leaderboard, then "Submit Your Project": share link + repo URL + deployed URL + code **`CreateOS-2026`** (case-sensitive)
- [ ] Stop committing at 5:30 PM PT. Commits after the lock don't count

Missing the video, the repo, a person on camera, or the public post means the project **is not judged**.

## 7. Demos (6:00 PM PT / 6:30 AM IST)
- [ ] Be online and ready to demo live if you make the top 5

## Links
- Hackathon + FAQ: https://createos.sh/hackathon
- Sandbox docs: https://createos.sh/docs/Sandbox/Quickstart · SDKs: https://createos.sh/docs/Sandbox/SDK/Overview · Limits: https://createos.sh/docs/Sandbox/Limits
- Full docs for agents: https://createos.sh/docs/llms-full.txt
- CreateOS on Stripe Projects: https://createos.sh/docs/Services/Stripe-Projects
- Stripe Projects docs: https://docs.stripe.com/projects · Providers: https://projects.dev/providers
- Stripe primer (Checkout, Billing, MPP, Link Agent Wallet): https://shipbysundown.dev/
- Sandbox API base: https://api-eu.sb.createos.sh/
- Help: Discord #support · hello@createos.sh
