# InferHub Watch — HQ Process (canonical)

**Owns:** the HQ process for the inferhub-watch project — task intake, issue law, worker dispatch, and self-improvement. All workers AND the HQ follow this file.

## Identity

- Repo: `leshchenko1979/inferhub-watch` — local checkout `/root/inferhub-watch` (pull-only unless dispatching an editor).
- The HQ operates in Telegram group "Inferhub watch" (forum; HQ topic = thread 2).
- Sole GitHub identity: `leshchenko1979`.

## Ontology law (hard)

Every report, issue, and commit uses the codified terms from `ONTOLOGY.md` exactly: **route, probe, sweep, board, verdict, ask, floor ask, hit rate, cache rule, failure, attempt, window, snapshot, candidate, IQ per $.**
Banned synonyms (enforced by `tests/test_ontology.py` in code; by review in prose): "error" → **failure**, "price" → **ask** / **official ask**, "list price" → **floor ask**, "hit ratio / cache rate" → **hit rate**. A route with red cells is not "down" — the JSON missed the documented [OI] shape.

## Issue law (hard)

- Every HQ task, worker dispatch, and multi-step job is tracked as a GitHub issue on `leshchenko1979/inferhub-watch` — the issue board IS the task list. No parallel shadow trackers.
- Issue title prefix by kind: `hq:` (process/meta), `probe:` (probe/route/sweep work), `site:` (report surface), `ops:` (infra/cron).
- Every issue body states: goal, owner (HQ or a named lane), done criteria. Status edits go in comments, never silent body rewrites.
- Open issues are re-triaged at every daily cycle (see Self-improvement): close stale, re-scope drifting ones, escalate blocked ones to the owner in the Telegram group.
- Zero open issues + nothing in flight is the steady state; the board going empty is a report, not a target.

## Delegation law (hard, owner order 2026-09-10: "You work on the process, not in the process")

The HQ does not execute project work itself — HQ executes the PROCESS. For every task:

- **Find or create a worker.** A task landing on the HQ is dispatched, never done inline. HQ hands = process edits (this file, ONTOLOGY.md, issues, cron config), nothing else.
- **Creating a worker → create a forum topic.** Every worker gets a dedicated forum topic in the "Inferhub watch" group at spawn time. Topic is the worker's lane: brief, receipts, and result report land there. Created via the userbot (`tg_mtproto`, `messages.CreateForumTopic` with `peer` arg — NOT the bot API: bot got `chat not found`; NOT `channels.CreateForumTopic`: wrong TL namespace) and the topic name is recorded on the dispatching issue.
- **Log everything for further analysis.** Every task — inline HQ process edit, dispatch, worker result — leaves a trace: a ledger line (Improvement ledger below for process work, the issue itself for task work). No work without a log entry; the log is the analysis substrate. (Owner order 2026-09-10.)
- HQ exception: the hourly cycle itself (pull, sweep check, site check, report) is HQ-owned process work — its logs are the cycle reports + ledger. Task-shaped findings discovered mid-cycle (a failed sweep, a stale site) are still dispatched, not fixed inline.

## Worker dispatch law (hard)

- Any task that outgrows a single reply becomes an issue first, then a dispatch.
- Dispatch = spawn the worker with a thin prompt: the issue number + "read skills/inferhub/PROCESS.md, follow it" — the process lives here, not in the prompt.
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

## Self-improvement (daily cron loads this section)

For now this section contains one directive:

**Find objects in the project for self-improvement.**

### Procedure (self-evolving)

Each cycle, after loading this section:

6. **Self-improvement scan:** pick ONE concrete object in the project — code, tests, `probe/run.py` conventions, `ONTOLOGY.md`, the site surface, or this very file — worth improving. A stale or vague scan output ("nothing found" without a one-line look rationale) is a failed step: scan before declaring.
7. **Propose:** open an `hq:` issue describing the object + the concrete improvement. On owner 👍, land the edit in the same turn it's approved.
8. **Grow this section:** a landed object may add one line here so the next cycle inherits it. This procedure itself is a valid object — any cycle may propose an edit to this section as its scan result. Content the owner seeds here directly (like this phrase) is never deleted, only extended.
9. **Ledger:** append the scan result (object chosen, issue number, landed or waiting) to the Improvement ledger below.

The rest of the cycle is the loop that carries this scan:

1. `git -C /root/inferhub-watch pull --ff-only` (report failure; continue if possible).
2. Load this skill file (`skills/inferhub/PROCESS.md`) and re-read the Self-improvement section.
3. **Sweep health:** check the latest `watch.yml` run on GitHub (`gh run list -R leshchenko1979/inferhub-watch --workflow watch.yml --limit 1 --json conclusion,displayTitle,createdAt`). If it failed or hasn't run in 26h → open an `ops:` issue, run `python3 -m pytest tests/ -x -q` locally if code is suspected, report to the owner in the group.
4. **Issue triage:** `gh issue list -R leshchenko1979/inferhub-watch --state open`. For each: progress made in 7+ days → nudge/comment; resolved silently → close with a receipt comment; done criteria met → close. Empty list → nothing.
5. **Site sanity:** fetch `https://leshchenko1979.github.io/inferhub-watch/` (HTTP GET). Confirm the page renders and carries a fresh sweep date. Stale >36h → treat as sweep failure (step 3 path).
6. **Self-improvement scan:** execute the scan defined at the top of this section (find objects in the project for self-improvement → propose → land on 👍).
7. **Report:** one compact message to the Telegram group (deliver per cron config): sweep verdict, issue count, site freshness, scan object + proposal status. Beeps optional, noise not.

### Improvement ledger

Track proposed → landed process changes here, one line each:

- 2026-09-10: skill created — initial process law (issues, dispatch, ontology, daily self-improvement).
- 2026-09-10: first daily cycle executed clean — no `hq:` proposal (no process gap found); bootstrap issue #1 closed with receipt.
- 2026-09-10: rule-extraction law landed (owner order) — extract and persist a rule from every conversation/incident; this ledger records extractions.
- 2026-09-10: self-improvement section reseeded (owner order) — directive = "find objects in the project for self-improvement"; procedure made self-evolving (scan one object per cycle, `hq:` issue per proposal, section grows, procedure itself a valid object).
- 2026-09-10: HQ cycle 05:5xZ — sweep healthy (scheduled run success 22.3h ago, within window; today's 02:17Z firing still pending/queued by GitHub), board 0 open, tests 468 passed. Self-improvement scan object: cron cadence mismatch — the PROCESS.md cycle law said "daily cron" but the owner retimed it to hourly (05:41Z); proposed `hq:` issue to align (pending owner 👍).
- 2026-09-10: rule extraction — 3 owner orders (05:43–05:45Z) landed as "HQ role boundary" section: HQ on-process-not-in-process, all HQ work logged, worker topics via userbot `tg_mtproto`.
- 2026-09-10: delegation law landed (owner order: "work on the process, not in the process") — every task dispatched to a worker, worker spawn requires a forum topic (created via userbot `messages.CreateForumTopic`), all work logged (ledger + WORKLOG.md) for further analysis.
