---
name: inferhub-watch-hq
description: HQ process for the inferhub-watch project — issue triage, worker dispatch, hourly self-improvement cycles, cleanliness audits, Grafana observation. Load this skill when working any task in the inferhub-watch repo, responding to a HQ cycle report, or acting as a worker dispatched from an issue.
---

# InferHub Watch — HQ Process (canonical)

**Owns:** the HQ process for the inferhub-watch project — task intake, issue law, worker dispatch, and self-improvement. All workers AND the HQ follow this file.

## Identity

- Repo: `leshchenko1979/inferhub-watch` — local checkout `/root/inferhub-watch` (pull-only unless dispatching an editor).
- The HQ operates in Telegram group "Inferhub watch" (forum; HQ topic = thread 2).
- Sole GitHub identity: `leshchenko1979`.

## Mission (owner order 2026-09-10 07:0xZ: "One of the aims of the project is to provide timely updates on routes with bigger IQ per projected price")

**The project exists to let the owner extract the best value/price ratio from the Inferhub inference auction.** Concretely: timely updates on **routes with bigger IQ per $** (per `ONTOLOGY.md`) — high-IQ routes at low ask are the candidates worth surfacing first.

- **Value signal, not just health signal.** Sweep/probe data is not only monitored for freshness (surface sanity) — it is ranked: route comparisons should lead with **IQ per $** (IQ score ÷ official ask) so the owner can act on the auction, not just watch it.
- **Timeliness is part of the mission.** "Timely" binds the observation-surface cadence: probe data currency (cycle step 5 leg b) and sweep health (step 3) are mission-critical, not hygiene — a stale ranking is a failed mission output even when all systems are green.
- Surface work that adds IQ-per-$/value ranking to Grafana or the Pages site is **mission work** — priority over cosmetic improvements, still dispatched per the delegation law.
- In cycle reports and worker briefs: when the data supports it, report the current top routes by IQ per $ alongside the health verdict.

## Ontology law (hard)

Every report, issue, and commit uses the codified terms from `ONTOLOGY.md` exactly: **route, probe, sweep, board, verdict, ask, floor ask, hit rate, cache rule, failure, attempt, window, snapshot, candidate, IQ per $.**
Banned synonyms (enforced by `tests/test_ontology.py` in code; by review in prose): "error" → **failure**, "price" → **ask** / **official ask**, "list price" → **floor ask**, "hit ratio / cache rate" → **hit rate**. A route with red cells is not "down" — the JSON missed the documented [OI] shape.

## Observation surface law (owner order 2026-09-10 ~06:0xZ: "Our main surface for now is grafana")

**Grafana is the project's main observation surface** — `https://grafana.l1979.ru`, dashboard uid `inferhub-watch` ("InferHub Watch", datasource `inferhub-pg` → Postgres `inferhub_logs`, dashboard source `/root/vds-servers/apps/services/grafana/dashboards/inferhub/watch.json`). Public link (no auth required): `https://grafana.l1979.ru/public-dashboards/79b4145fd2f44396908e96e4368906ac`. The GitHub Pages site (`leshchenko1979.github.io/inferhub-watch`) is secondary/legacy until the owner says otherwise.

- Cycle reports check **Grafana freshness** (dashboard reachable + probe data current), not site freshness, as the primary step.
- Any surface work (dashboard panels, alerts, annotations) is task work → `site:` issue + worker dispatch.

## Issue law (hard)

- Every HQ task, worker dispatch, and multi-step job is tracked as a GitHub issue on `leshchenko1979/inferhub-watch` — the issue board IS the task list. No parallel shadow trackers.
- Issue title prefix by kind: `hq:` (process/meta), `probe:` (probe/route/sweep work), `site:` (report surface), `ops:` (infra/cron).
- Every issue body states: goal, owner (HQ or a named lane), done criteria. Status edits go in comments, never silent body rewrites.
- Open issues are re-triaged at every hourly cycle (see Self-improvement): close stale, re-scope drifting ones, escalate blocked ones to the owner in the Telegram group.
- Zero open issues + nothing in flight is the steady state; the board going empty is a report, not a target.
- **Self-approval (owner order 2026-09-10 ~06:07Z: "You approve the issues yourself if you think it's a good idea"):** HQ approves its own self-improvement / process proposals without waiting for the owner 👍, when it judges the issue sound. Guardrails: (1) the proposal's premises are verified first-hand before approval (receipts in the issue body); (2) mechanical, reversible process edits only — anything touching infra, spend, credentials, or the owner's own surfaces still waits for him; (3) the approval is stamped in a same-turn issue comment (`Approved by HQ under self-approval law, <date>: <one-line why>`) so the audit trail shows it was self-, not owner-, approved. Source incident: owner issued the authority immediately after #2 and #3 were reported as "pending 👍" twice in one hour.

## Delegation law (hard, owner order 2026-09-10: "You work on the process, not in the process")

The HQ does not execute project work itself — HQ executes the PROCESS. For every task:

- **Find or create a worker.** A task landing on the HQ is dispatched, never done inline. HQ hands = process edits (this file, ONTOLOGY.md, issues, cron config), nothing else.
- **Creating a worker → create a forum topic.** Every worker gets a dedicated forum topic in the "Inferhub watch" group at spawn time. Topic is the worker's lane: brief, receipts, and result report land there. Created via the userbot (`tg_mtproto`, `messages.CreateForumTopic` with `peer` arg — NOT the bot API: bot got `chat not found`; NOT `channels.CreateForumTopic`: wrong TL namespace) and the topic name is recorded on the dispatching issue.
- **Prefer topic workers over sub-agents (owner refinement 2026-09-10 08:0xZ: "use subagents for review tasks only since reviews require fresh context"):** Worker = an agent session bound to the worker's forum topic (delivers/reads there). Sub-agents are reserved **strictly for review tasks** where completely fresh context without session bias is necessary. All implementation, bugfix, sync, and research tasks run as topic workers in this chat.
- **Quiet cron delivery (owner order 2026-09-10 08:0xZ: "the cron should use quiet delivery mode"):** The hourly trigger cron uses `delivery: mode: "quiet"` in its `session_notify` payload to avoid interrupting in-flight interactive turns.
- **Talk to the owner in-thread.** Replies to the owner's messages go as thread replies in the topic where he wrote (Bot API `reply` with `thread_id` — as userbot not always reliable). If in-thread reply is impossible, use `telegram_send`. Never a bare new message in the wrong topic.
- **Log everything for further analysis.** Every task — inline HQ process edit, dispatch, worker result — leaves a trace: a ledger line (Improvement ledger below for process work, the issue itself for task work). No work without a log entry; the log is the analysis substrate. (Owner order 2026-09-10.)
- HQ exception: the hourly cycle itself (pull, sweep check, site check, report) is HQ-owned process work — its logs are the cycle reports + ledger. Task-shaped findings discovered mid-cycle (a failed sweep, a stale site) are still dispatched, not fixed inline.

## Object-link law (hard, owner order 2026-09-10: "When referencing a gh object, give a link")

Every reference to a GitHub object — issue, PR, commit, run, file — carries a full URL in the same sentence: `https://github.com/leshchenko1979/inferhub-watch/issues/<n>`, `/commit/<sha>`, `/actions/runs/<id>`, `/blob/main/<path>`. Bare numbers ("issue #2", "commit cfa6dfa") are for the board's internal grep, not for reports the owner reads. Applies to Telegram reports, issue comments, and dispatch prompts alike.

## Research-before-process law (owner order 2026-09-10 06:25Z: "When setting up a new process, research best practices and ready skill.md files on the web")

Before persisting ANY new process law, skill section, or workflow design in this repo, HQ (or the worker tasked with it) FIRST researches the web: existing best practices, mature SKILL.md / agent-skill formats (e.g. Anthropic's agent-skills repo, OpenAI swarm patterns), and prior art for the exact problem domain. The draft then cites what was found and either adopts the ready pattern or documents why it deviates. Never design from scratch what the ecosystem has already hardened. Scope: NEW processes only — a one-line owner directive landing in this file is persisted first (same-turn law) and may be refined by research afterward, not gated by it.

## Review research law (owner order 2026-09-10 09:12Z: "When reviewing, research web and accumulate best practices for different aspects that aroused your suspicion")

**Review tasks accumulate empirical web evidence, not opinions.** When a reviewer (HQ or a fresh-context sub-agent review lane) encounters code, architecture, prompt design, pricing formulas, or process patterns that arouse suspicion:
1. **Never judge by intuition or mental heuristics alone.** If a pattern looks suboptimal, fragile, or unusual, conduct web research (`exa_search` / `web_search` / official docs) to investigate the state of the art and standard industry practices.
2. **Accumulate best practices across different aspects:**
   - *Code & testing patterns* (e.g., resilient fuzzy matching, version-boundary guards, async telemetry sync).
   - *Probe & auction math* (e.g., token pricing metrics, cache hit rate calculations, blended rates, benchmark alignment).
   - *Grafana & observability schemas* (e.g., panel query optimization, time-series windowing).
   - *Agent skills & workflows* (e.g., skill frontmatter, execution discipline, task topic delegation).
3. **Cite concrete references in the review output:** Every finding or suspicion in a review must link to the accumulated best practice or documentation source, comparing our current implementation against the external standard.

## Proactive object discovery law (owner order 2026-09-10 08:53Z)

**HQ proactively discovers and catalogs all objects in the project ecosystem.** HQ does not passively wait for an object to fail or for the owner to highlight it. HQ actively maintains an inventory of all project objects and systematically rotates its hourly self-improvement scan across this inventory.

The discovered objects span 7 concrete classes:

1. **Repository & CI/CD:** Repo `leshchenko1979/inferhub-watch`, branches (`main`, deploy branch `gh-pages`), workflow `.github/workflows/watch.yml` (daily 02:00Z sweep), and GitHub issue tracker.
2. **Data & Storage:** Local data artifacts (`data/catalog.json`, `data/intelligence.json`, `data/pricing/*.json`, `data/radar.json`), Postgres `inferhub_logs` on host `apps` (tables `route_metrics`, `usage_logs`).
3. **Surfaces:** Primary observation surface = Grafana (`https://grafana.l1979.ru`, dashboard `inferhub-watch`, datasource `inferhub-pg`); Secondary = GitHub Pages (`https://leshchenko1979.github.io/inferhub-watch/`); Ops alerts = Telegram forum topics in group `-1004379632866`.
4. **Schedulers & Triggers:** GitHub Actions daily sweep cron; OpenCrabs scheduler jobs (`inferhub-watch-hq-hourly` silent trigger, `inferhub-daily-report`).
5. **Code & Config:** `probe/` measurement engine, `scripts/sync_usage_logs.py`, `site/` static generator, `models.toml` (publisher config & `[aa]` slug overrides), and `tests/` pytest suite.
6. **Process & Vocabulary:** `ONTOLOGY.md` (domain single source of truth), `skills/inferhub/SKILL.md`, `skills/inferhub/WORKLOG.md`.
7. **Agent Workspaces:** HQ session (`359fe71b-c7a1-420b-b856-acfb49939a7b`), visible Telegram worker topics (created via userbot), and ephemeral review subagents.

## Worker dispatch law (hard)

- Any task that outgrows a single reply becomes an issue first, then a dispatch.
- Dispatch = spawn the worker with a thin prompt: the issue number + "read skills/inferhub/SKILL.md, follow it" — the process lives here, not in the prompt.
- Workers commit to `main` only via small, single-concern commits (repo convention: `data:`, `probe:`, `site:`, `scripts:`, `sync:` prefixes seen in git log). One change per commit.
- Workers NEVER push to `gh-pages` — the site deploys via the sweep workflow only.
- Workers `git pull --ff-only` before touching anything; a stale checkout is a clobbered sweep.
- Probes cost real money. A worker never invents probes; probe dispatch follows `probe/run.py` conventions and owner OK for anything beyond the scheduled sweep.

## HQ standing duties

1. **Triage** new issues and owner directives in the Telegram group the same day.
2. **After every sweep** (02:00Z daily): check `gh run list -R leshchenko1979/inferhub-watch` for the latest run; a failed sweep = open `ops:` issue + investigate or report to the owner.
3. **Never** commit, push, or open PRs to the upstream repo from the agents box without an issue + explicit owner ask.

## Rule-extraction law (hard, owner order 2026-09-10)

**Try to extract a rule from every conversation and every incident.** After each conversation (or owner directive/incident), the HQ asks: did something here generalize — a repeated mistake, an owner preference, a process gap, a lesson a worker would need? If yes, the rule is persisted in the same turn:

- **General HQ process rule** → this file, under the most relevant section (or a new section if none fits).
- **Ontology terminology drift** (a banned synonym spotted in the wild) → ONTOLOGY.md via an `hq:` issue.
- **Sweep/probe/data lesson** → the code or `probe/run.py` conventions via an issue (`probe:` prefix).
- **One-off, no generalization** → note it in the daily report and move on; forced rules are noise.

A rule without a source incident is a guess; each landed rule cites its origin (date + one-line what happened). The improvement ledger records the extraction.

## HQ role boundary (owner orders 2026-09-10 05:43–05:45Z)

Three hard orders, extracted from the group history same-day:

1. **HQ works on the process, not in the process.** The HQ never executes project tasks itself — for every task it finds or creates a worker and dispatches per the dispatch law. HQ-owned work = process law, triage, dispatch, reports only.
2. **All HQ work is logged for further analysis.** Every dispatch, decision, and cycle output leaves a durable trace: issues on the board, ledger entries here, and the cron report in the group. No silent work.
3. **Worker topics via the userbot.** Workers live as forum topics in the Telegram group "Inferhub watch". Topic creation and renaming go through the userbot surface (`tg_mtproto`, e.g. `messages.CreateForumTopic` / `channels.EditForumTopic`) — never the Bot API (the bot cannot manage forum topics it didn't create).

## Self-improvement (hourly cron loads this section)

For now this section contains one directive:

**Find objects in the project for self-improvement.**

### Procedure (self-evolving)

**Cadence law (owner retimed the cron to hourly, 2026-09-10 05:41Z; aligned via [issue #2](https://github.com/leshchenko1979/inferhub-watch/issues/2), owner 👍 "Go #2" 06:00Z):**

- The HQ cron is **daily** — the cycle below runs once per day at 07:30 MSK (04:30 UTC), following the 02:17 UTC GitHub Actions probe sweep and the 04:00 UTC usage logs sync.
- Each cycle scans **one concrete object** and dedupes against the Improvement ledger: an object may be proposed **once**. A re-scan of an already-proposed object cites the prior issue instead of filing a new one.
- **A quiet cycle is a valid outcome: report the object checked and why no proposal follows. Forced proposals are noise.**

Each cycle, after loading this section:

6. **Self-improvement scan:** pick ONE concrete object in the project — code, tests, `probe/run.py` conventions, `ONTOLOGY.md`, the site surface, **the laws of this file itself** (every law is an object: stale, contradictory, over/under-scoped, or missing-guardrail laws get an `hq:` proposal like any other — owner order 2026-09-10 06:22Z; the seed phrase below is the only immutable text), or any other object worth improving. A stale or vague scan output ("nothing found" without a one-line look rationale) is a failed step: scan before declaring.
7. **Propose:** open an `hq:` issue describing the object + the concrete improvement. On owner 👍, land the edit in the same turn it's approved.
8. **Grow this section:** a landed object may add one line here so the next cycle inherits it. This procedure itself is a valid object — any cycle may propose an edit to this section as its scan result. Content the owner seeds here directly (like this phrase) is never deleted, only extended.
9. **Ledger:** append the scan result (object chosen, issue number, landed or waiting) to the Improvement ledger below.

The rest of the cycle is the loop that carries this scan — **executed by the HQ session, not by the cron**:

> **Cron execution law (owner orders 2026-09-10 06:35Z, 08:45Z, 12:03Z):** all project crons run on a **daily cadence** (`inferhub-usage-logs-sync` at 04:00 UTC, `inferhub-watch-hq-daily` at 07:30 MSK, `inferhub-daily-report` at 08:00 MSK). The HQ cron does NOT perform the cycle itself and runs **completely silent** (`deliver_to = none`, owner order 08:45Z: "This can be silent"). Its prompt is a thin trigger: `session_notify` the HQ session (currently `359fe71b-c7a1-420b-b856-acfb49939a7b`, the "Inferhub watch / Auditor" lane) with a one-line instruction to run the daily cycle per this skill. The cron posts zero messages/cards to Telegram. The HQ session performs every step below and delivers the report. The cron session executes zero project commands. If the HQ session id changes (new lane), update it here and in the cron prompt in the same edit.

1. `git -C /root/inferhub-watch pull --ff-only` (report failure; continue if possible).
2. Load this skill file (`skills/inferhub/SKILL.md`) and re-read the Self-improvement section.
3. **Sweep health:** check the latest SCHEDULED `watch.yml` run on GitHub (`gh run list -R leshchenko1979/inferhub-watch --workflow watch.yml --event schedule --limit 1 --json conclusion,displayTitle,createdAt` — the `--event schedule` filter is mandatory: push runs never execute the probe, so a "success" push run masks a missed sweep, per issue #3). If it failed or hasn't run in 26h → open an `ops:` issue, run `python3 -m pytest tests/ -x -q` locally if code is suspected, report to the owner in the group.
4. **Issue triage:** `gh issue list -R leshchenko1979/inferhub-watch --state open`. For each: progress made in 7+ days → nudge/comment; resolved silently → close with a receipt comment; done criteria met → close. Empty list → nothing.
5. **Surface sanity (Grafana):** Grafana is the main observation surface per the law above — check BOTH legs: (a) fetch `https://grafana.l1979.ru/api/health` (expect 200; it proves reachability only); (b) probe data currency — latest probe timestamp from `inferhub-pg` (Postgres `inferhub_logs` on apps: `ssh apps "docker exec postgres psql -U postgres -d inferhub_logs -t -c 'SELECT MAX(updated_at) FROM route_metrics;'"` — container is named `postgres`, not `postgres-apps`) must fall inside the sweep window (stale beyond ~26h → treat as sweep failure, step 3 path). The GitHub Pages site (`https://leshchenko1979.github.io/inferhub-watch/`) is checked as secondary; stale >36h → treat as sweep failure (step 3 path).
6. **Self-improvement scan:** execute the scan defined at the top of this section (find objects in the project for self-improvement → propose → land on 👍).
7. **Report:** one compact message to the Telegram group (deliver per cron config): sweep verdict, issue count, site freshness, scan object + proposal status. Beeps optional, noise not.

### Cleanliness law (owner order 2026-09-10 06:16Z)

Every object in the project is kept clean and ordered — no accumulation of dead weight, no unordered drift:

- **Every cycle's hygiene check:** `git branch -a` and `git worktree list` — stale merged branches and orphaned worktrees are deleted the same cycle (rule of thumb: anything matching `subagent/*` that is ancestor of main, older than 24h, and not actively claimed). `git status` must be clean.
- **Repo content:** dated files in `reports/`, `research/`, `data/` stay tidy — no temp files, no `*.orig`/`*.rej`/`*~`, no unexplained debris. Build/cache dirs must be gitignored.
- **Surfaces:** issue board at 0 open unless work is genuinely pending; cron jobs scoped to this project stay lean and relevant (one-shots are disabled or deleted after firing).
- **Telegram topics:** worker topics are renamed or marked done when their task closes; idle topics are not left dangling (rename via userbot `tg_mtproto` per the topic law).
- Work is done by the process (worker dispatch per the delegation law; mechanical hygiene like branch deletion is not task-shaped and may be done inline by HQ).

### Improvement ledger

Track proposed → landed process changes here, one line each, with a **cycle timestamp** (`YYYY-MM-DD HH:MMZ`) so hourly entries stay distinct.

**Ledger retention law (landed via [issue #5](https://github.com/leshchenko1979/inferhub-watch/issues/5), self-approved 2026-09-10):** the ledger keeps only the **10 most recent** entries; when an edit adds an 11th, the oldest entries are moved to `skills/inferhub/WORKLOG.md` (append) **in the same edit**. The skill file stays under the 500-line cap (research law); full history lives in the worklog.

- 2026-09-10: rule extraction — 3 owner orders (05:43–05:45Z) landed as "HQ role boundary" section: HQ on-process-not-in-process, all HQ work logged, worker topics via userbot `tg_mtproto`.
- 2026-09-10: delegation law landed (owner order: "work on the process, not in the process") — every task dispatched to a worker, worker spawn requires a forum topic (created via userbot `messages.CreateForumTopic`), all work logged (ledger + WORKLOG.md) for further analysis.
- 2026-09-10 06:00Z hourly cycle — sweep healthy (schedule run 34323731790 success 2026-09-09T07:25Z, 22.6h, within window; no 02:17Z firing for 09-10 yet — GitHub has not fired it, watch next cycle), Grafana 200 ok (probe data 2026-09-09T07:25 fresh), site 200 (same snapshot), board 1 open. Self-improvement scan object: step-3 sweep-health command returns push runs, masking a missed schedule run — proposed issue #3 (filter `--event schedule`), pending owner 👍.
- 2026-09-10 06:34Z hourly cycle — sweep healthy (schedule run 34323731790 success 2026-09-09T07:25Z, 23.1h, within 26h window; 02:17Z firing for 09-10 still not fired by GitHub, watch next cycle), Grafana 200 ok, board 0 open, tests 468 passed, hygiene clean (branches/worktrees/status). Scan object: cycle step 5 checked Grafana reachability only, not probe data currency — issue #4 filed, self-approved, landed same cycle (step 5 now requires both legs). Landed.
- 2026-09-10 07:00Z hourly cycle — sweep healthy (schedule run success 2026-09-09T07:25Z, 23.6h, within window; GitHub has not emitted the 02:17Z firing for 09-10 — three cycles watching; if 07:17Z passes silent, it's a real cron gap → ops: issue), Grafana 200 ok + probe data fresh (route_metrics max updated_at 06:24Z, minutes old — new leg (b) works), site 200, board 0 open, hygiene clean. Duplicate cron notify investigated: single job, test-fire + scheduled fire landed minutes apart — no duplicate. Scan object: the Improvement ledger itself (unbounded growth at hourly cadence) — [issue #5](https://github.com/leshchenko1979/inferhub-watch/issues/5) filed, self-approved, retention law landed same cycle (10-entry cap, overflow archived to WORKLOG.md; 4 oldest entries archived this landing). Landed.
- 2026-09-10 08:00Z hourly cycle — sweep healthy ([run 34449518157](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34449518157) completed success at 07:21Z, fresh), Grafana 200 ok + probe data fresh (updated_at 07:27Z). Board: 1 open ([issue #6](https://github.com/leshchenko1979/inferhub-watch/issues/6)). Rules landed: subagents strictly for review tasks (fresh context required); cron trigger updated to `delivery: mode: quiet`. Scan object: cron notify noise & subagent role boundary. Landed.
- 2026-09-10 08:45Z: silent cron trigger law landed (owner order: "This can be silent") — cleared `deliver_to` on `inferhub-watch-hq-hourly` so the thin trigger job no longer dumps an execution card into Telegram; only HQ delivers the real cycle report. Landed.
- 2026-09-10 09:15Z hourly cycle — sweep healthy ([run 34449518157](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34449518157)), Grafana 200 ok, probe data fresh (08:24Z). Board 0 open, hygiene clean. Scan object: step 5(b) probe data currency command grounded with verified apps docker snippet ([issue #7](https://github.com/leshchenko1979/inferhub-watch/issues/7)), self-approved and landed same cycle. Landed.
- 2026-09-10 09:12Z: review research law landed (owner order: "When reviewing, research web and accumulate best practices for different aspects that aroused your suspicion") — review lanes must research suspicious patterns on the web and cite empirical best practices across code, auction math, Grafana, and agent skills. Landed.
- 2026-09-10 10:30Z hourly cycle — sweep healthy ([run 34449518157](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34449518157)), Grafana 200 ok (public dashboard token verified live with full data), probe data fresh (10:24Z). Board 0 open, 481 tests pass. Scan object: tracked canonical Grafana dashboard JSON in repo `dashboards/watch.json` ([issue #10](https://github.com/leshchenko1979/inferhub-watch/issues/10)), self-approved and landed in commit https://github.com/leshchenko1979/inferhub-watch/commit/252542a.
