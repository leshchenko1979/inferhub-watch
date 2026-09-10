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

## Worker dispatch law (hard)

- Any task that outgrows a single reply becomes an issue first, then a dispatch.
- Dispatch = spawn the worker with a thin prompt: the issue number + "read skills/hq/PROCESS.md, follow it" — the process lives here, not in the prompt.
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

## Self-improvement (daily cron loads this section)

Each daily cycle, the firing session MUST execute all of:

1. `git -C /root/inferhub-watch pull --ff-only` (report failure; continue if possible).
2. Load this skill file (`skills/hq/PROCESS.md`) and re-read the Self-improvement section.
3. **Sweep health:** check the latest `watch.yml` run on GitHub (`gh run list -R leshchenko1979/inferhub-watch --workflow watch.yml --limit 1 --json conclusion,displayTitle,createdAt`). If it failed or hasn't run in 26h → open an `ops:` issue, run `python3 -m pytest tests/ -x -q` locally if code is suspected, report to the owner in the group.
4. **Issue triage:** `gh issue list -R leshchenko1979/inferhub-watch --state open`. For each: progress made in 7+ days → nudge/comment; resolved silently → close with a receipt comment; done criteria met → close. Empty list → nothing.
5. **Site sanity:** fetch `https://leshchenko1979.github.io/inferhub-watch/` (HTTP GET). Confirm the page renders and carries a fresh sweep date. Stale >36h → treat as sweep failure (step 3 path).
6. **Self-improvement proposal:** reflect on the past day: what broke, what triage caught late, what a worker had to ask twice. If a process gap exists → propose a concrete edit to THIS file as an issue (`hq:` prefix) and, on owner 👍, land it.
7. **Report:** one compact message to the Telegram group (deliver per cron config): sweep verdict, issue count, site freshness, any proposal. Beeps optional, noise not.

### Improvement ledger

Track proposed → landed process changes here, one line each:

- 2026-09-10: skill created — initial process law (issues, dispatch, ontology, daily self-improvement).
- 2026-09-10: first daily cycle executed clean — no `hq:` proposal (no process gap found); bootstrap issue #1 closed with receipt.
- 2026-09-10: rule-extraction law landed (owner order) — extract and persist a rule from every conversation/incident; this ledger records extractions.
