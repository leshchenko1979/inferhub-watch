# Redesign working notes — 2026-09-01 pass (regroup, de-crowd)

Audit of what is simultaneously visible today (desktop 1440):
1. Ticket: eyebrow + 3 text lines + big number + 300px sparkline + alternate. Teal side stripe.
2. Probe results BEFORE the board: 8 model groups, all `open` — 8 full 5-column tables
   with repeated theads (~40 header cells) + chips + pills. This owns the first screen.
3. Board: 5 columns — route (3 stacked lines: code, ask, sparkline) | Δ ask in/out
   (2 colored arrows) | effective pair (2 numbers + bar) | IQ | IQ per $.
   7+ competing numbers per row.
4. Spend block (3 big stats + 387px sparkline) sits ABOVE the board table.
5. Eyebrow on every section (Verdict, Board, Evidence, Evidence).

Phone 390: same order; board starts ~900px down. Tables fold to cards, but
IQ / IQ-per-$ cells fold UNLABELED (no data-label).

## Regroup plan (same data, less crowd)

Page order: Verdict → Board → Probe results → Past runs → Method.
Interview: ticket leads, glance = ticket + board decision rows; probe groups are depth.
(Conflicts with PRODUCT.md "probe results is the front page" — the 2026-09-01 interview
supersedes; flagged for owner review.)

Board decision row 5 → 3 columns: Route | effective $/M pair | IQ per $.
- Δ ask in/out column → plumbing entry "Δ ask in / out" (same spans/titles).
- IQ column → plumbing entry "IQ" (IQ/$ stays; it is the ranking number).
- Ask-history sparkline → plumbing entry "ask history".
- Route cell keeps code + dim ask line (floor-ask `*` stays visible at glance).
Plumbing becomes the one-stop per-route detail, grouped: price movement, history,
source, intelligence, health, window totals, retries.

Rate pair: label BOTH eras — bold basis + tag, dim alternate + tag ("now"/"30d").
Understandable without the caption: big number is what the board ranks on, each
number carries its era.

Probe results: groups closed by default (summary chips already carry the
per-family verdict: name · tests · price). 8 tables hide behind one tap.

Spend: collapse into a details row below the board table ("Spend — $X month to
date"); stats + sparkline inside. History belongs after the decision surface.

Color law enforcement (red = failures only):
- rate bars: >$0.20 was red → cap at amber (pricey = warning).
- cache bars: <40% was red → cap at amber (low cache = cost warning).
- price chip: challenger −15% was red → amber (warning, not failure);
  magnitude stays in the chip text. Conflicts with PRODUCT.md line "turns the
  model's price chip red" — flagged for owner review.

Quiet chrome (bans): remove ALL eyebrows; remove ticket's teal side-stripe
border (banned pattern + color as decoration).

Mobile fold: board card = route header + 2 labeled cells (effective $/M,
IQ per $ — gains data-label) + full-width plumbing. No horizontal scroll.

## Self-critique vs bans list
- side-stripe borders: removing the ticket one, adding none. ✓
- gradient text / glassmorphism / hero-metric template: none introduced. ✓
- identical card grids: no new grids; plumbing dl keeps auto-fit. ✓
- eyebrow-on-every-section: all removed. ✓
- numbered markers without sequence: none. ✓
- border-radius 32px+: none introduced (existing pill chips untouched). ✓
- decorative grids / repeating-linear-gradient / border+blur combo: none. ✓
- boldness budget: ticket only; everything else loses chrome. ✓
- contrast: new pair-tag uses existing --muted at caption size (already ≥4.5:1
  on --surface); no new low-contrast text. ✓
- motion: none added; prefers-reduced-motion block untouched. ✓

## Test impact (update only where structure genuinely changes)
- order assertions flipped (results<pricing → pricing<results) + one slice fix.
- model-group `open` assertion → closed.
- test_rundata color caps (1.3 → mid, 0.0 cache → mid); test_radar chip bad → mid.
- Everything else stays asserted as-is.
