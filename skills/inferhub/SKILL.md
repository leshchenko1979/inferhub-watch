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
- **Timeliness is part of the mission.** "Timely" binds the observation-surface cadence: dashboard data currency (cycle step 5 leg b, refreshed hourly by the `inferhub-usage-logs-sync` cron) and sweep health (step 3) are mission-critical, not hygiene — a stale ranking is a failed mission output even when all systems are green.
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
- **Owner ruling vs codified gate (hard, owner ruling 2026-09-11, [issue #15](https://github.com/leshchenko1979/inferhub-watch/issues/15) → [issue #16](https://github.com/leshchenko1979/inferhub-watch/issues/16)):** when an owner ruling conflicts with a codified gate or threshold, never satisfy it by loosening the threshold — that launders the ruling into a fake certification. Change what the gate MEASURES so it passes honestly, or surface the conflict to the owner. Corollary: any value that decides pass/fail (a tolerance, a landing bar, a minimum sample) belongs to the owner, and is never chosen by HQ or a worker to produce the desired verdict. Origin: the owner ruled projected canonical and ordered the gate re-tuned; every constant tweak was measured and rejected (a tolerance wide enough to pass certifies a 63.5%-wrong projection), so the gate's metric — not its constants — had to change.

## Delegation law (hard, owner order 2026-09-10: "You work on the process, not in the process")

The HQ does not execute project work itself — HQ executes the PROCESS. For every task:

- **Find or create a worker.** A task landing on the HQ is dispatched, never done inline. HQ hands = process edits (this file, ONTOLOGY.md, issues, cron config), nothing else.
- **Creating a worker → create a forum topic.** Every worker gets a dedicated forum topic in the "Inferhub watch" group at spawn time. Topic is the worker's lane: brief, receipts, and result report land there. Created via the userbot (`tg_mtproto`, `messages.CreateForumTopic` with `peer` arg — NOT the bot API: bot got `chat not found`; NOT `channels.CreateForumTopic`: wrong TL namespace) and the topic name is recorded on the dispatching issue.
- **Agent communication law (hard, owner order 2026-09-10 13:11Z: "Agents don't see what telegram_send posts. To talk to agents, use session_notify"):** Agents do not see what `telegram_send` posts. When briefing, instructing, or passing task data to an agent session (including topic workers), use `session_notify` targeting the worker session UUID. Telegram topics provide visibility and archival for the owner, while `session_notify` delivers the live in-context brief to the agent.
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
4. **Schedulers & Triggers:** GitHub Actions daily sweep cron (`.github/workflows/watch.yml`, 02:00Z); OpenCrabs scheduler jobs in the `ops` profile (list with `opencrabs cron list -p ops`) — `inferhub-watch-hq-hourly` (hourly thin trigger, silent), `inferhub-usage-logs-sync` (hourly at `:23`; the only writer of `route_metrics`), `inferhub-daily-report` (08:00 MSK).
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

- The HQ cron is **hourly** — every cycle below runs once per hour, not once per day.
- Each cycle scans **one concrete object** and dedupes against the Improvement ledger: an object may be proposed **once**. A re-scan of an already-proposed object cites the prior issue instead of filing a new one.
- **A quiet cycle is a valid outcome: report the object checked and why no proposal follows. Forced proposals are noise.**

Each cycle, after loading this section:

6. **Self-improvement scan:** pick ONE concrete object in the project — code, tests, `probe/run.py` conventions, `ONTOLOGY.md`, the site surface, **the laws of this file itself** (every law is an object: stale, contradictory, over/under-scoped, or missing-guardrail laws get an `hq:` proposal like any other — owner order 2026-09-10 06:22Z; the seed phrase below is the only immutable text), or any other object worth improving. A stale or vague scan output ("nothing found" without a one-line look rationale) is a failed step: scan before declaring.
7. **Propose:** open an `hq:` issue describing the object + the concrete improvement. On owner 👍, land the edit in the same turn it's approved.
8. **Grow this section:** a landed object may add one line here so the next cycle inherits it. This procedure itself is a valid object — any cycle may propose an edit to this section as its scan result. Content the owner seeds here directly (like this phrase) is never deleted, only extended.
9. **Ledger:** append the scan result (object chosen, issue number, landed or waiting) to the Improvement ledger below.

The rest of the cycle is the loop that carries this scan — **executed by the HQ session, not by the cron**:

> **Cron execution law (owner orders 2026-09-10 06:35Z, 08:45Z):** the hourly cron does NOT perform the cycle itself and runs **completely silent** (`deliver_to = none`, owner order 08:45Z: "This can be silent"). Its prompt is a thin trigger: `session_notify` the HQ session (currently `359fe71b-c7a1-420b-b856-acfb49939a7b`, the "Inferhub watch / Auditor" lane) with a one-line instruction to run the hourly cycle per this skill. The cron posts zero messages/cards to Telegram. The HQ session performs every step below and delivers the report. The cron session executes zero project commands. If the HQ session id changes (new lane), update it here and in the cron prompt in the same edit.

1. `git -C /root/inferhub-watch pull --ff-only` (report failure; continue if possible).
2. Load this skill file (`skills/inferhub/SKILL.md`) and re-read the Self-improvement section.
3. **Sweep health:** check the latest SCHEDULED `watch.yml` run on GitHub (`gh run list -R leshchenko1979/inferhub-watch --workflow watch.yml --event schedule --limit 1 --json conclusion,displayTitle,createdAt` — the `--event schedule` filter is mandatory: push runs never execute the probe, so a "success" push run masks a missed sweep, per issue #3). If it failed or hasn't run in 26h → open an `ops:` issue, run `python3 -m pytest tests/ -x -q` locally if code is suspected, report to the owner in the group.
4. **Issue triage:** `gh issue list -R leshchenko1979/inferhub-watch --state open`. For each: progress made in 7+ days → nudge/comment; resolved silently → close with a receipt comment; done criteria met → close. Empty list → nothing.
5. **Surface sanity (Grafana):** Grafana is the main observation surface per the law above — check BOTH legs: (a) fetch `https://grafana.l1979.ru/api/health` (expect 200; it proves reachability only); (b) dashboard data currency — latest `route_metrics` timestamp from `inferhub-pg` (Postgres `inferhub_logs` on apps: `ssh apps "docker exec postgres psql -U postgres -d inferhub_logs -t -c 'SELECT MAX(updated_at) FROM route_metrics;'"` — container is named `postgres`, not `postgres-apps`). That timestamp is written **hourly by the `inferhub-usage-logs-sync` cron** (`sync_route_metrics()` in `scripts/sync_usage_logs.py`) and NOT by the sweep — the sweep writes JSON to `data/` and never touches Postgres — so it tracks that cron, never sweep health. Stale beyond ~2h → the sync cron has failed: open an `ops:` issue against `inferhub-usage-logs-sync` and report to the owner. Sweep health is step 3's business alone. The GitHub Pages site (`https://leshchenko1979.github.io/inferhub-watch/`) is checked as secondary; stale >36h → treat as sweep failure (step 3 path).
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

- 2026-09-10: delegation law landed (owner order: "work on the process, not in the process") — every task dispatched to a worker, worker spawn requires a forum topic (created via userbot `messages.CreateForumTopic`), all work logged (ledger + WORKLOG.md) for further analysis.
- 2026-09-10 13:10Z hourly cycle — sweep healthy ([run 34449518157](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34449518157)), Grafana 200 ok (probe currency 12:23Z), Pages 200 ok. Board: 1 open ([issue #11](https://github.com/leshchenko1979/inferhub-watch/issues/11)). Scan object: Class 5 Code & Config — scripts/sync_usage_logs.py catalog write path dirties tracked data/catalog.json. Filed [issue #11](https://github.com/leshchenko1979/inferhub-watch/issues/11), self-approved, created Telegram forum topic 257 (Worker — #11 Cleanliness & Sync), dispatched. Oldest entry archived to WORKLOG.md.
- 2026-09-10 18:00Z hourly cycle — sweep healthy ([run 34449518157](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34449518157)), Grafana 200 ok (probe data 17:23Z), Pages 200 ok. Board: 1 open ([issue #13](https://github.com/leshchenko1979/inferhub-watch/issues/13)). Top IQ/$ route: `zai/glm-5.3-flash` (IQ 41.9, 272,964 IQ/$), followed by `ag/gemini-3.8-flash-high` (52,820 IQ/$). Scan object: Class 5 Code & Config — `scripts/auto_route_switch.py` lacks fallback to `keys.toml` for `bot_token`. Filed [issue #13](https://github.com/leshchenko1979/inferhub-watch/issues/13), self-approved, created Telegram forum topic 343 (`Worker — #13 keys.toml Fallback`), bound session and dispatched via session_notify. Oldest entry archived to WORKLOG.md.
- 2026-09-10 ~18:4xZ owner directive — owner ordered the switcher's alert path moved off raw Bot API: "It should use opencrabs session notify instead" (supersedes [issue #13](https://github.com/leshchenko1979/inferhub-watch/issues/13) token machinery; #13 was already resolved by commit [ee55843](https://github.com/leshchenko1979/inferhub-watch/commit/ee55843)). Filed [issue #14](https://github.com/leshchenko1979/inferhub-watch/issues/14), created Telegram forum topic 357 (`Worker — #14 session_notify alerts`), spawned worker `b0864cce` (`3e589b77-f469-4fda-a0e4-96b074c17630`). Landed in [0a4b750](https://github.com/leshchenko1979/inferhub-watch/commit/0a4b7504386f5f96424a27edc61d37825595d06c): deleted `get_bot_token` / `send_telegram_notification`, added dynamic target resolution (`notify_session` override → `session_bindings` active group session → fallback) and receipt-checked `opencrabs session notify --confirm`. 512 tests passed, [issue #14](https://github.com/leshchenko1979/inferhub-watch/issues/14) closed.
- 2026-09-11 07:5xZ hourly cycle — sweep healthy ([run 34573828600](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34573828600), 07:19Z), Grafana 200 ok (probe data 07:28Z), Pages 200 ok, board 0 open at cycle start, cleanliness clean (main + gh-pages only, single worktree). Value: board top IQ/$ `cbcn/glm-5.3-flash` (IQ 41.9, 83,800), then `ag/gemini-3.8-flash-high` (82,400). Scan object: Class 3 Surfaces — the four Grafana IQ/$ panels use an **ungated** projected basis while the board gates projected behind `projection_gate` (closed, 44/109 within 20%), so the same route reads 240,233 on Grafana vs 82,400 on the board. Filed [issue #15](https://github.com/leshchenko1979/inferhub-watch/issues/15), self-approved (premises verified first-hand), created Telegram forum topic 399 (`Worker — #15 IQ/$ basis alignment`), spawned worker `b31e40b6` (`c34f5cbb-4baa-43c9-814e-7fab4b7d249b`), briefed via `session_notify`. Canonical-basis choice left to the owner (mission says "projected", gate says not yet earned).
- 2026-09-11 08:2xZ owner directive — owner ruled **projected canonical** ("re-tune the gate so both surfaces use it"), settling the #15 canonical-basis question. HQ measured before touching anything: `projection_gate` scores absolute deviation, but ONTOLOGY.md line 39 defines its job as certifying the IQ/$ **ranking** — median rho 0.918, rho>=0.5 on 14/14, yet rho>=0.8 on only 9/14 = 0.643, and every rank row also fails `GATE_MIN_N=20` because a rank gate's unit is the transition (n=14), not the route-pair (n=109). No constant tweak passes honestly (tol would need 63.5%; the projection is unbiased at median ratio 0.9487; the board's own smoothed estimator scores worse at 0.303). Closed [issue #15](https://github.com/leshchenko1979/inferhub-watch/issues/15) — its divergence defect is fixed in [18d9f1e](https://github.com/leshchenko1979/inferhub-watch/commit/18d9f1e6df741f63b1329a795afd0ef64a073133), verified first-hand by HQ (gate `f|109|44|0.404|0.2`, 17 route bases, 537 tests, HEAD == origin/main). Filed [issue #16](https://github.com/leshchenko1979/inferhub-watch/issues/16) (gate re-tune onto rank fidelity), self-approved, created topic 421, spawned worker `1e7660d6` (`45bac1d7-59b9-4a56-b698-ac0fdf8081ed`), briefed via `session_notify`. The rho bar is the owner's to set and is NOT to be tuned into a pass.
- 2026-09-11 09:0xZ hourly cycle — sweep healthy ([run 34573828600](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34573828600), 07:19Z), Grafana 200 ok (dashboard data 08:24Z), Pages 200 ok, board 1 open ([issue #16](https://github.com/leshchenko1979/inferhub-watch/issues/16), gate re-tune, awaiting the owner's rho bar). Value: board top IQ/$ `cbcn/glm-5.3-flash` (IQ 41.9, 83,800), then `ag/gemini-3.8-flash-high` (IQ 41.2, 82,400). Scan object: Class 4 Schedulers & Triggers — the Class 4 inventory named 2 of 3 live jobs (missing `inferhub-usage-logs-sync`), and cycle step 5(b) read `route_metrics.updated_at` as "probe data currency" when that table is written hourly by the sync cron and never by the sweep (so the leg could not detect a missed sweep, and mis-routed staleness to the sweep). Filed [issue #17](https://github.com/leshchenko1979/inferhub-watch/issues/17), self-approved, landed in this file same cycle.
- 2026-09-11 09:2xZ hourly cycle — sweep healthy ([run 34573828600](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34573828600), 07:19Z — the only **scheduled** run; the 08:15–09:22 runs are push events), Grafana 200 ok (dashboard data 08:24Z, sync cron healthy), Pages 200 ok, board 2 open at cycle start ([#16](https://github.com/leshchenko1979/inferhub-watch/issues/16) gate re-tune awaiting the owner's rho bar; [#17](https://github.com/leshchenko1979/inferhub-watch/issues/17) fix landed). Closed [#17](https://github.com/leshchenko1979/inferhub-watch/issues/17) — both criteria verified first-hand (3 live crons match the Class 4 inventory; step 5(b) names the sync cron as the sole `route_metrics` writer). Value: board top IQ/$ `cbcn/glm-5.3-flash` (IQ 41.9, 83,800), then `ag/gemini-3.8-flash-high` (IQ 41.2, 82,400). Scan object: Class 1 Repository & CI/CD — `.github/workflows/watch.yml` declares no `run-name`, so a push run is titled with the commit subject while a sweep run is titled `Watch`; both carry `name=Watch` and `conclusion=success`, and a push run **skips** `probe`, so a green push run proves nothing about the sweep. HQ's own step-3 read fell into exactly this trap this cycle (reported push run 34583855265 as the sweep; the genuine scheduled sweep was 34573828600). Filed [issue #18](https://github.com/leshchenko1979/inferhub-watch/issues/18) — the **structural** half of [#3](https://github.com/leshchenko1979/inferhub-watch/issues/3), which fixed only the procedure — self-approved (premises verified first-hand), created Telegram forum topic 444 (`Worker — #18 CI run legibility`), spawned worker `9c8de03e` (`5c5d7214-9764-4b72-b837-d8de64e2d283`), briefed via `session_notify`. Topic hygiene per the cleanliness law: renamed topics 399 (#15), 257 (#11), 288 (#12), 45 (#2) to `Done — …`.
- 2026-09-11 09:4xZ owner ruling "1+3" — owner picked option 1 (drop the TOOLS.md carrier-dispatch note) and option 3 (retire the Spearman rho criterion; the projection gate becomes a **TOP-1 CROWN GATE at 15% tolerance**) from HQ's 3-option keyboard, then corrected HQ's process: "And why haven't you delegated this work?" Option 3 is project work, so it was **dispatched, not done inline** — ruling recorded on [issue #16](https://github.com/leshchenko1979/inferhub-watch/issues/16#issuecomment-5632703722), Telegram forum topic 465 (`Worker — #16 top-1 crown gate`) created via userbot `messages.CreateForumTopic`, worker `4f72ca76` (`0628f7d3-4e4b-43f0-bc1b-1e984871fe7a`) spawned, session bound in `session_bindings`; the substantive brief followed via `resume_agent` (the initial `session_notify` routed but did not wake the worker, which had already completed a reconnaissance-only turn — the spawn prompt is only the thin seed). Crown definition pinned from the code, not guessed: the crown is the **lowest projected $/M**, because the board's `_iq_sort_key` returns `(eff_val, -(iqps))` — price PRIMARY, IQ/$ only a tiebreaker within an equal cost band; the switcher's own ordering is IQ/$ descending, a *different* crown, recorded as a docstring note with the switcher untouched. Estimator pinned to `probe.basis.projected` (the single money-basis owner). The crown gate: per transition, does the route the board would have crowned realize a cost within 15% of the true cheapest? 0% → 11/14 FAIL, **15% → 12/14 PASS**, 60% → 13/14, 120% → 14/14; 15% is the switcher's own `SWITCH_THRESHOLD`. Retires `_spearman` + `GATE_RHO_BAR`; the rho row `pass=f | n=14 | land=9 | share=0.643 | bar=0.8 | min_n=10 | rho_median=0.9` stands until the worker lands. Option 1 executed: the TOOLS.md note dropped. Topic hygiene: 421 renamed `Done — #16 gate rank fidelity`.
- 2026-09-11 10:0xZ hourly cycle — sweep healthy ([run 34573828600](https://github.com/leshchenko1979/inferhub-watch/actions/runs/34573828600), 07:19Z — still the only **scheduled** run; the 09:33–09:59 runs are push events), Grafana 200 ok (dashboard data 09:25Z, sync cron healthy), Pages 200 ok, board 2 open at cycle start ([#16](https://github.com/leshchenko1979/inferhub-watch/issues/16) crown-gate re-tune in flight; [#18](https://github.com/leshchenko1979/inferhub-watch/issues/18) run-name fix landed, awaiting the 02:17Z sweep). All three inferhub crons verified live in the `ops` profile (`inferhub-daily-report` 05:00Z, `inferhub-usage-logs-sync` 09:23Z, `inferhub-watch-hq-hourly` 10:00Z). Value: board top IQ/$ `cbcn/glm-5.3-flash`, then `ag/gemini-3.8-flash-high`. Scan object: **Class 2 Data & Storage** — the one class never scanned — the `data/` dated-artifact naming convention: `probe/cache_ramp.py` line 104 hardcodes its output filename to `cache_ramp_20260905.json`, the **only** hardcoded output filename in the codebase, while every other dated artifact derives from the clock ([`probe/run.py:208`](https://github.com/leshchenko1979/inferhub-watch/blob/main/probe/run.py) → `data/runs/<ISO>.json`; [`probe/pricing.py:730`](https://github.com/leshchenko1979/inferhub-watch/blob/main/probe/pricing.py) → `data/pricing/<date>.json`), so a re-run on a later date overwrites a **tracked** record whose name asserts a date the payload does not have. Filed [issue #19](https://github.com/leshchenko1979/inferhub-watch/issues/19), self-approved (premises verified first-hand: sole hardcode, artifact tracked, no consumer reads it by name), created Telegram forum topic 476 (`Worker — #19 cache_ramp dated output`), spawned worker `2bb2e9b9` (`ad6f4b4a-0c26-4614-8621-815bd5016c8e`), session bound, briefed via `session_notify`. `data/runs/` growth was examined in the same scan and deliberately **NOT** proposed — those files are load-bearing history (`probe/pricing.py:416` rebuilds sweep windows from them, `probe/radar.py:29` reads the newest, `scripts/sync_usage_logs.py:54` flags rows inside known probe-run windows).
