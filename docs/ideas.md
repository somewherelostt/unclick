# Official idea list (CreateOS x Stripe hackathon), summarized

Features marked * are inferred, not stated in the PDF.

## Builders
1. **Stripe Webhook Fixer**: fires Stripe test events at a webhook handler and patches the code until every event passes.
2. **Client Landing Page in 10 Minutes**: a brief plus a logo becomes a live page, and the client pays a Stripe deposit to get the code.
3. **Docs Snippet Tester**: runs every code example on a docs page and flags broken ones with a fix.

## Money
4. **Stripe Revenue Analyst**: MRR, churn and cohort charts from Stripe data.
5. **Payout Reconciler**: matches Stripe payouts to bank statements.
6. **Timesheet to Invoice**: turns a time-tracking export into a real Stripe invoice.

## Safe
7. **Refund Agent with Guardrails**: issues Stripe refunds under a spending cap, with human approval.
8. **"Should I Install This?"**: tests a package with the network locked down and returns a safe or risky verdict.

## Computer use
9. **New User Onboarding Tester**: a browser agent signs up like a stranger, watchable live.
10. **Accessibility Auditor**: a browser agent walks a site and writes an a11y report with fixes.

## Always on
11. **Founder's Morning KPI Email**: wakes daily, pulls Stripe data, emails charts, then sleeps (pause/resume*).
12. **Alert Responder**: wakes on an alert, runs health checks, and emails a diagnosis.

## Advanced
13. **Code Mode for Stripe**: one run-code tool instead of 20 tools, with token use compared.
14. **Agent That Sets Up Its Own Stack**: a one-line spec becomes a live app with DB and login via Stripe Projects.
15. **Stack Bake-off**: provisions Neon, Turso and PlanetScale via Stripe Projects, benchmarks them, keeps the winner.
16. **Pay-per-run Code Runner**: other agents pay per run via Stripe Machine Payments Protocol (test mode).
17. **Autoresearch in an Afternoon**: an edit, run, score, keep-or-undo loop, about 40 iterations (fork/snapshot*).
18. **Agent That Writes Its Own Tools**: builds and tests new tools when it hits a gap.
19. **Eval Builder**: turns a topic into a scored test and ranks 3 models.
20. **Fork-and-Race Fixer**: a repo with a failing test is forked into 4 sandboxes, and the tests pick the winning fix.
21. **Red Team with Proof**: attacks a vulnerable app in a sealed lab, patches it, and proves the fix.
22. **Swarm Scored by a Test Suite**: a planner sends tasks to coder agents, each in its own sandbox.
23. **Computer Use on an App with No API**: clicks through a browser live, then writes a script to replace the clicking.
24. **Bug Fixer That Reproduces First**: takes a Sentry error, reproduces it, fixes it, and opens a PR with proof.

## Judging hints
Working demos, a real user problem, real use of CreateOS features (network allowlists, pause/resume, fork, public URLs, desktop), Stripe Projects setup, and submission to projects.dev leaderboard (code `CreateOS-2026`).
