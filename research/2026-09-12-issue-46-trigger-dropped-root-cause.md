# #46 — the hourly HQ trigger is silently dropped: root cause

**Issue:** https://github.com/leshchenko1979/inferhub-watch/issues/46
**Job:** `inferhub-watch-hq-hourly` · id `5c960cfb-16a5-4a35-918b-5acd1d30336f` · `0 * * * *` UTC · `profile_name=ops` · **`deliver_to = None`**
**Cron session:** `84458ab1-1ee5-4d5a-b71d-b3be58eddd71`
**Written:** 2026-09-12 ~18:5xZ

## 0. The symptom, precisely

The trigger fires hourly. It is a thin wrapper whose whole job is one
`session notify` to the HQ session `359fe71b-c7a1-420b-b856-acfb49939a7b`.
Between **11:00Z and 17:00Z inclusive** the notify was **blocked, not executed**
— seven consecutive hours of a dead trigger — and **nothing reported anywhere**.

At 18:00Z it fired successfully with no intervention. That recovery is what
made the failure look intermittent. It is not.

## 1. Three defects, chained

The outage is the product of three independent defects. Each is sufficient on
its own to break the trigger; together they make it silent and self-sustaining.

### D1 — The A2A gateway is load-coupled and intermittently times out

`127.0.0.1:18791` is not a service — it is **bound by the ops daemon itself**:

```
$ ss -ltnp | grep 18791
LISTEN 0 128 127.0.0.1:18791 0.0.0.0:* users:(("opencrabs",pid=544821,fd=42))
```

pid 544821 = `/usr/local/bin/opencrabs -p ops -d daemon`, config
`[a2a] enabled=true, bind="127.0.0.1", port=18791`
(`/root/.opencrabs/profiles/ops/config.toml:373-377`).

The listener therefore shares the daemon's tokio runtime with every agent turn
in that profile. Measured on a trivial `GET /a2a/v1`, five samples, same
process, seconds apart:

| sample | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| seconds | 0.367 | 0.565 | 1.066 | 0.846 | 2.312 |

At the same moment the ops daemon sat at **47.6 % CPU / 31 threads**, with other
lanes' turns in flight (`run_tool_loop` counts in the tail: `2fae1230` 335,
`03cf724c` 209). A `session notify` landing in a busy window times out, and the
CLI reports it as:

```
❌ transport_error: cannot reach the A2A gateway at http://127.0.0.1:18791/a2a/v1
   : error sending request for url (...) (exit 4)
```

**This is the real 10:00:57Z failure.** It is not boot-specific: `triage-sweep`
hit the same `transport_error` at **18:25:04Z**, with no restart anywhere near
it.

There is a *second*, independent gateway race — see D4.

### D2 — The Layer-3 retry guard is permanent by construction  ← load-bearing

`/root/opencrabs/src/brain/tools/bash.rs`:

```rust
// :408 — the block path
// Layer 3: short-circuit if the agent just ran this exact command and it failed.
if let Some(msg) = check_recent_failure(context.session_id, &input.command) {
    return Ok(ToolResult::error(msg));      // <-- returns WITHOUT recording
}
```

```rust
// :1245-1262 — the recorder. REPLACE semantics: one entry per unique command.
pub(crate) fn record_bash_outcome(session_id, command, failed, error_snippet) {
    let buf = state.entry(session_id).or_default();
    let normalized = command.trim().to_string();
    buf.retain(|o| o.command.trim() != normalized);   // evict the old entry
    buf.push_back(RecentBashOutcome { command, failed, error_snippet });
    while buf.len() > RECENT_BASH_WINDOW { buf.pop_front(); }   // window = 5
}
```

`check_recent_failure` blocks only while an entry for that exact command is
`failed == true`. `record_bash_outcome` is the **only** writer (three call sites,
all in `bash.rs`: `:395`, `:418`, `:852`).

**A blocked call never reaches `:852`, so it never rotates the buffer.** In a
session whose *only* bash command is the repeated one — which is exactly what
this thin trigger is — a `failed=true` entry is **never replaced and never
evicted**: the FIFO never fills, because nothing else is ever pushed. The block
is therefore **permanent for the life of the process**.

That is the seven-hour outage: one transient D1 timeout at 10:00:57 poisoned the
session, and every subsequent hour was refused before the command ran.

The guard's own unit test passes and *should*:

```rust
// src/tests/bash_retry_loop_test.rs
fn outcome_can_flip_from_fail_to_success() {
    record_bash_outcome(sid, "make build", true,  Some("err"));
    assert!(check_recent_failure(sid, "make build").is_some());
    record_bash_outcome(sid, "make build", false, None);
    assert!(check_recent_failure(sid, "make build").is_none());   // PASSES
}
```

The logic is correct in isolation — a *later success* flips the entry. The live
defect is that **a blocked call never gets the chance to be that later success**.

**A second, compounding effect:** the block message embeds the previous error
verbatim —

```
You already ran this exact command in the last 5 bash calls and it failed. ...
Previous error:
❌ transport_error: cannot reach the A2A gateway at http://127.0.0.1:18791/a2a/v1 ...
```

— so the agent, told to *"report the error text only"*, relayed a **seven-hour-old
gateway error as if it were current**. Every run report from 11:00 to 17:00 says
"the A2A gateway is unreachable". It was not. The guard was. This is what made
the outage read as a gateway problem.

### D3 — Nothing is delivered, so a failure is invisible

`cron_job_runs.content` proves the reports were written **every hour**:

| started_at (UTC) | run content (head) |
|---|---|
| 18:00:02 | `Silence on success.` |
| 17:00:16 | `The command failed — the notify could not be sent: ❌ transport_error …` |
| 16:00:02 | `Command failed with exit 4: ❌ transport_error …` |
| 15:00:40 | `Command failed — reporting error text only: ❌ transport_error …` |
| 14:00:42 | `Command failed (non-zero exit). Error text: …` |
| 13:00:35 | `The notify command exited non-zero: …` |
| 12:00:30 | `Command failed (exit 4): …` |
| 11:00:19 | `The notify command failed — the A2A gateway is unreachable: …` |
| 10:00:56 | `Command failed with exit code 4: …` |
| 09:00:21 | `Notification delivered (id bc07c37d, rc=0) — no action needed.` |

The evidence existed for seven hours. Two reasons nobody saw it:

1. **`deliver_to = None`** on the job — the run's final message has **no delivery
   target**, so `content` is written to `cron_job_runs` and goes no further.
2. The prompt's own boundary: *"Silence on success. If the command exits
   non-zero, report the error text only."* — **a guard block is not a non-zero
   exit**, so even a working delivery path would have carried nothing.

## 2. The restart paradox — resolved (and it was never the buffer)

The block survived four `opencrabs-ops.service` restarts (13:13:04, 13:44:04,
15:58:32, 16:41:15) although `recent_bash_state()` is *"in-memory only; cleared
on process restart"*. The explanation is **scheduler-lock ownership**:

```
$ ls -l /proc/*/fd | grep scheduler
  default.lock   -> pid 544821  /usr/local/bin/opencrabs -p ops -d daemon
  family.lock    -> pid 544821
  oc134probe.lock-> pid 544821
  ops.lock       -> pid 523008  /usr/local/bin/opencrabs daemon      <-- DEFAULT profile
```

`ops.lock` mtime = **2026-09-12 17:03:18**, i.e. the moment the **default** daemon
restarted (journalctl: `17:03:17 opencrabs.service ... Started`).

`ui.rs:152-163` — *"One scheduler per profile machine-wide (#444) … polling the
same cron_jobs table from two schedulers double-fires every due job"* — means the
daemon that loses the race **skips that profile's scheduler**. So:

- The **default** daemon held `ops.lock` from before 10:00Z until 17:03:17, and
  it ran the ops cron all day. Its buffer carried the 10:00:57 failure.
- Every `opencrabs-ops.service` restart was **irrelevant** — that daemon did not
  own the ops scheduler. At 17:26:09 the ops daemon started, found `ops.lock`
  taken, and skipped its own profile's scheduler.
- At **17:03:17** the default daemon restarted → **empty buffer** → the
  **18:00:10** fire succeeded.

No mysterious persistence is needed. The buffer lived in a process nobody was
restarting. **The 18:00 recovery was a restart of the wrong daemon for the right
reason.**

## 3. D4 — the boot-ordering race (a second, independent gateway failure mode)

The daemon starts its cron schedulers **before** it binds the A2A listener.
Measured across every boot today:

| boot | `Cron scheduler started` (scheduler.rs:422) | `A2A Gateway starting` (server.rs:198) | gap |
|---|---|---|---|
| 13:13:04 | 13:13:04.601 → 13:13:14.915 | 13:14:02.949 | ~48 s |
| 13:44:04 | 13:44:04.708 → 13:44:19.049 | 13:44:54.412 | ~35 s |
| 15:58:05 | 15:58:06.581 → 15:58:21.778 | 15:59:25.514 | ~64 s |
| 16:41:15 | 16:41:16.219 → 16:41:35.487 | 16:42:36.431 | ~61 s |
| 17:26:10 | 17:26:11.182 → 17:26:42.360 | 17:27:36.786 | ~54 s |

`is_due` (`scheduler.rs:549-557`): *"If next_run_at is set and is in the past
(or now), it's due"* — a job whose `next_run_at` elapsed during a restart fires
on the **first tick**, inside that window, into an unbound port. Same
`transport_error` signature as D1. Not the cause of the 10:00Z event, but a
guaranteed failure mode on any restart near the top of an hour.

## 4. Fix design (to HQ's done criteria)

> *"The trigger either delivers every hour, or a failed delivery raises a visible
> alert (a 🔴-marked message to the owner topic, or an `ops:` issue filed
> automatically)."*

| # | Fix | Kind | Where |
|---|---|---|---|
| **F1** | Set `deliver_to` on `5c960cfb-…` so **any** run report reaches a visible surface. Without this, no fix below is observable. | config | `cron_manage update` on the job |
| **F2** | Reword the prompt: report on **any non-success**, not only a non-zero exit — a guard block is not a non-zero exit. | config | same job |
| **F3** | **Make the Layer-3 block non-permanent.** Either record the blocked outcome, or bound the entry by time rather than call-count, or evict on block. `bash.rs:408` is the one-line locus; `record_bash_outcome` semantics (`:1245`) are the design question. | **code** | `/root/opencrabs` → worker lane |
| **F4** | Bind the A2A listener **before** the cron schedulers start (or gate a due job on gateway readiness). | **code** | `/root/opencrabs` → worker lane |
| **F5** | The `session notify` client should retry with backoff on `transport_error` — the gateway is load-coupled (D1), so a single attempt is not a fair test of reachability. | **code** | `/root/opencrabs` → worker lane |

F3 is the load-bearing one: it converts a transient blip into a permanent
outage. F1 is the cheapest and closes the visibility hole immediately.

**Not applied.** `/root/opencrabs` is read-only from this lane; config on a live
trigger is HQ's call.

## 5. Live experiment (prediction on record)

With `ops.lock` held by the default daemon since 17:03:18, and the 18:00:10 fire
having succeeded, the session buffer now holds `failed=false` for the command.

**Prediction: the 19:00Z fire SUCCEEDS.** If it is blocked instead, D2's
mechanism is incomplete and a success does not in fact replace the entry —
which would make the defect strictly worse than described here.

## 6. Evidence receipts

- Guard source: `/root/opencrabs/src/brain/tools/bash.rs:395,408,418,852,1190-1262`
- Guard unit test: `/root/opencrabs/src/tests/bash_retry_loop_test.rs` (`outcome_can_flip_from_fail_to_success`)
- Scheduler lock: `/root/opencrabs/src/config/profile.rs:837-862`, `src/cli/ui.rs:152-163, 1995`
- Scheduler tick: `/root/opencrabs/src/cron/scheduler.rs:411,422,437,444,447,448,549-557`
- A2A bind: `/root/opencrabs/src/a2a/server.rs:174,198-200`
- Listener: `ss -ltnp | grep 18791` → pid 544821 fd 42
- Locks: `/root/.opencrabs/locks/scheduler/{ops,default,family,oc134probe}.lock`
- Restarts: `journalctl --since 2026-09-12T00:00` (ops ×6, default 17:03:17)
- Run history: `cron_job_runs` where `job_id like '5c960cfb%'`
- Transcript: `messages` where `session_id='84458ab1-1ee5-4d5a-b71d-b3be58eddd71'`

**Log-window caveat:** the ops daily log was truncated in place at
`2026-09-12T12:54:01Z` by `/usr/local/bin/opencrabs-log-guard.sh`. Nothing before
that timestamp survives in it, so the 10:00:57 event is evidenced from
`cron_job_runs` and the session transcript, **not** from the log.
