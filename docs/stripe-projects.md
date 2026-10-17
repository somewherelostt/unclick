# Stripe Projects - practical guide (CreateOS x Stripe hackathon)

Legend: [V] verified on an official page fetched 2026-10-18; [I] inferred / unconfirmed.
Note: projects.dev/hackathon-participants is client-rendered (a plain fetch returns the leaderboard shell). I read its content from the page's JS bundle, so it is [V] but extracted from source.

## 1. What it is
- [V] Stripe Projects = a plugin for the Stripe CLI that provisions third-party services from the terminal, creates provider accounts for you, returns real credentials, and handles billing through Stripe. It does not generate app code. Free to use; you pay providers at their rates. Status: developer/public preview (flags may change).
- [V] ~68 providers. Categories and examples (slug = `provider/service`):
  - AI: OpenRouter `openrouter/api`, ElevenLabs, Composio, Chatbase, Athena, HeyGen, Supermemory
  - Sandbox/compute/hosting: CreateOS `createos/project`, E2B `e2b/sandboxes`, Daytona, Blaxel, Vercel `vercel/project`, Cloudflare, Netlify, Railway, Render, Fly.io
  - DB: Neon `neon/postgres`, Supabase `supabase/project`, Turso, PlanetScale, Prisma, ClickHouse, Chroma; cache: Upstash
  - Auth: Clerk `clerk/auth`, Auth0, WorkOS. Search: Exa, Firecrawl, Algolia, Parallel, Perplexity
  - Browser: Browserbase, Kernel, Steel. Payments: Stripe `stripe/livemode`, Metronome, Privy, RevenueCat
  - Email/comms: Resend, AgentMail, AgentPhone, Twilio. Analytics: PostHog, Mixpanel, Amplitude
- [V] Credentials: providers return them to the CLI, then an encrypted copy goes to `.projects/vault/vault.json` plus the Stripe Secret Store, and plaintext goes to `.env` (perm 600, auto-added to `.gitignore`). `env --pull` re-syncs; it also runs automatically after add/rotate/upgrade.
- [V] `stripe projects init` creates: `.projects/state.json` (commit), `.projects/state.local.json` (commit, despite the name), `.projects/vault/` (don't commit), `.env` (don't commit), `AGENTS.md`, `CLAUDE.md`, `.agents/skills`.
- [V] `.env` is local only; Projects does NOT push env vars to your production host. Copy them into Vercel/CreateOS/etc. settings yourself.

## 2. Windows setup, step by step
1. Prereqs: Node 18+ (for the npm route and `npx skills`). A Stripe account is free; `init` can create one in-browser. [V]
2. Install Stripe CLI (pick one; docs want CLI v1.43.3+) [V]:
   - `winget install Stripe.StripeCLI`
   - `scoop bucket add stripe https://github.com/stripe/scoop-stripe-cli.git` then `scoop install stripe`
   - `npm install -g @stripe/cli` (what projects.dev recommends; the universal option)
   - Direct: GitHub releases `stripe_X.X.X_windows_x86_64.zip`, unzip, add the folder with `stripe.exe` to PATH (AV may false-flag it).
   - Verify: `stripe --version` (open a NEW terminal after install so PATH refreshes) [I]
3. Login: `stripe login` (browser pairing code). `stripe login --interactive` for API-key mode. [V]
   - Gotcha: on CLI > v1.50.0 an account admin must enable "MCP and CLI access" at dashboard.stripe.com/settings/mcp-and-cli-access. [V]
   - `stripe projects init` also triggers sign-in/account creation, so a separate login may be optional. [V per hackathon guide]
4. Plugin: `stripe plugin install projects`; update with `stripe plugin upgrade projects` (organizer-recommended, confirmed in docs.stripe.com/cli/projects). Check: `stripe projects --help`. [V]
5. Optional agent skill (for Claude Code): `npx skills add https://docs.stripe.com --skill stripe-projects -g -y`. [V]
6. In an empty project dir: `stripe projects init my-app`. Backup if the CLI fails: https://dashboard.stripe.com/projects/new (organizer-supplied; I could not verify it) [I]
7. Discover: `stripe projects catalog` | `stripe projects catalog createos` | `stripe projects search sandbox`
8. Provision: `stripe projects add createos/project`, `stripe projects add openrouter/api`, `stripe projects add vercel/project` [V slugs]
9. Creds: `stripe projects env` (names only, values hidden), `stripe projects env --pull` (writes `.env`).
10. Other commands [V]: `status`, `list`, `pull <projectID>`, `services list`, `link <provider>`, `open <provider>`, `rotate <p>/<s>`, `remove <p>/<s>`, `upgrade|downgrade|update`, `billing add|show|update`, `spend`, `share`, `import <url>`, `init --from <url>`, `llm-context`, `variables set <name> --env-key KEY --value V`, `env create|use|list`.
11. Agent/CI flags [V]: `--json --no-interactive --auto-confirm --accept-tos --quiet --debug`. Error `PROVIDER_NOT_LINKED` -> `stripe projects link <provider>`.
- Windows tip [I]: if PowerShell blocks `npx`/scripts, use cmd or Git Bash, or `Set-ExecutionPolicy -Scope Process Bypass`. Quote JSON for `--config` carefully in PowerShell.
- Free tier needs no card. Paid tiers need `billing add` and a supported country (long list incl. US, GB, EU, AU, CA, SG, AE, BR; check docs.stripe.com/projects/paid-tier-countries for yours). [V] Organizer says Stripe Payments activation is NOT needed outside the US. [organizer note]

## 3. CreateOS and LLM providers
- [V] CreateOS IS a provider: `stripe projects add createos/project` (categories Sandbox/Hosting/Compute; "GPU and CPU on demand"). Listed on docs.stripe.com/projects, projects.dev and projects.dev/llms.txt.
- [V] CreateOS's own docs (createos.sh/docs/Services/Stripe-Projects): add creates a CreateOS account and injects credentials into `.env`; verify with `createos whoami`; plans via `stripe projects add createos/pro-monthly`; change with `stripe projects update createos-plan --service <free|beginner-monthly|pro-monthly|enterprise-monthly>` (Free $0, Beginner $10, Pro $75, Enterprise $200 per month); cancel with `stripe projects remove createos-plan --yes`; `stripe projects open createos` opens the dashboard.
- [UNKNOWN] Which env var names get written, and whether one is the SANDBOX key. The doc page names none. Run `stripe projects env` right after `add` and look for something like `CREATEOS_SANDBOX_API_KEY` / `CREATEOS_API_KEY`.
- [V] The sandbox SDK reads `CREATEOS_SANDBOX_API_KEY` (and optional `CREATEOS_SANDBOX_BASE_URL`); `npm i computesdk @computesdk/createos-sandbox` or `@nodeops-createos/sandbox` (docs.computesdk.com/providers/createos-sandbox). Those docs say the key is generated in the CreateOS dashboard. [I] If the Projects-issued key is the account key it may work; if not, put the dashboard sandbox key in `.env` as a project variable: `stripe projects variables set createos-sandbox-key --env-key CREATEOS_SANDBOX_API_KEY`.
- LLM: [V] the only general-LLM gateway in the catalog is OpenRouter (`openrouter/api`: one API for hundreds of models, routed by cost/speed/capability). There is no direct `anthropic/` or `openai/` provider in the catalog I fetched. [I] So use OpenRouter with model ids like `anthropic/claude-...` or `openai/gpt-...`, and keep your own ANTHROPIC_API_KEY as a fallback via `variables set`. Run `stripe projects search anthropic` and `search openai` to confirm live.
- Other AI providers [V]: ElevenLabs (TTS), Composio (tool integrations), Supermemory (memory), Chatbase, HeyGen, Athena. (The hackathon page mentions Hugging Face and Sentry in an example, but they are not in the catalog; [I] likely stale.)

## 4. Leaderboard submission and what scores
Submission flow [V, from the participant guide]:
1. App must be deployed on a hosting provider (or published to npm for libraries); repo must be public.
2. `stripe whoami` -> copy your Stripe account ID (required).
3. `stripe projects share` -> share URL (https://projects.dev/s/...; encodes services only, no secrets).
4. projects.dev/leaderboard -> "Submit Your Project": paste share link + public repo URL + deployed URL.
5. Hackathon code field: `CreateOS-2026` (case-sensitive, type exactly; the guide also supports `?hackathon-code=` URL prefill). It tags your entry so the leaderboard can filter by event.
- [V] Leaderboard ranking is by community upvotes (participants vote on the winner). Entries show name, builder, repo, stack (providers), votes, X/LinkedIn share buttons. Sign-in (Clerk) is probably needed to submit/vote [I].
- Official judging criteria for CreateOS prizes were not on any page I could read. [UNKNOWN] Ask the organizers.

"Meaningful use" [I - not officially defined; inferred from the guide]:
- Provision the real stack via Stripe Projects (visible in `.projects/state.json` and the share link), not hand-signed-up accounts; commit `state.json` and `state.local.json`.
- Use 3-5 providers that each have a reason, e.g. `createos/project` (code-exec sandbox), `openrouter/api` (LLM), `neon/postgres` or `supabase/project` (run history), `vercel/project` or CreateOS hosting (deploy), optionally `exa/api`, `firecrawl/api`, `browserbase/project` as agent tools.
- Show usage in the README: `stripe projects status`, `stripe projects spend`, a stack diagram. Keep the generated `AGENTS.md`/skills in the repo.

## 5. Monetization angle that would impress judges
Products are [V] (shipbysundown.dev + Stripe docs); the wiring is [I].
- Cheapest strong demo: Stripe Checkout / Payment Links (`stripe payment_links create --line-items price=price_123`) to sell "agent run credits"; test mode is fine.
- Usage-based billing: Stripe Billing meters, or Metronome (`metronome/sandbox` in Projects: meter events, price usage, bill in real time). Bill per sandbox execution-second; a natural fit for "agents that run code".
- Machine Payments Protocol (MPP) / x402: return HTTP 402 and accept per-call agent payments (a pay-per-run API endpoint), as described in Stripe's agentic commerce primer.
- Link Agent Wallet: `npm i -g @stripe/link-cli`, `@stripe/link-sdk`; scoped credentials, spend limits, user approval. An agent that buys its own compute/API is a strong story.
- Meta angle: Projects pays providers with Shared Payment Tokens and supports spend caps (`stripe projects billing update --limit 5 --provider openrouter`). Show a budget-capped agent.
- Advice [I]: ship ONE thing end to end (Checkout test mode -> credits -> sandbox run metered via Metronome/Stripe meter) rather than everything.

## Sources
- https://projects.dev , https://projects.dev/hackathon (guide) , https://projects.dev/hackathon-participants , https://projects.dev/llms.txt , https://projects.dev/providers/
- https://docs.stripe.com/projects , https://docs.stripe.com/cli/projects , https://docs.stripe.com/cli/login , https://docs.stripe.com/projects/paid-tier-countries
- https://github.com/stripe/stripe-cli#installation , https://shipbysundown.dev/
- https://createos.sh/docs/Services/Stripe-Projects/ , https://docs.computesdk.com/providers/createos-sandbox
