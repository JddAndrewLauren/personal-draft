# Handoff: keep Yahoo's draft queue loaded with the optimizer's top picks

Written 2026-09-04 evening. Real draft **Sat 2026-09-05 11:30 PDT**, 45 s clock.

## Goal

A per-tick browser step that stars the optimizer's top two or three available players in the
Yahoo draft room's Queue, so that if a clock expires Yahoo's autodraft takes the app's choice
instead of its own, and the team never drops into autopick mode.

## Why (docs/mock-draft-2026-09-04b.md, app/loop findings)

In mock #2 two expired clocks (picks 99, 118) put the team into Yahoo **autopick mode**; once
every team autopicks the room finishes the remaining rounds in about a minute. Two misses = the
rest of the draft is lost. A pre-loaded queue is the only fallback that survives that.

## What is known about the room (verified live)

- Players tab table holds only the top ~100 rows by rank; late targets (TE3, DEF, K) are not in
  the DOM. Rows carry `data-id` = Yahoo player id (matches `YahooPlayerID` in players.csv).
- The search box is a React input: set the value with the native setter and dispatch `input`.
  Search results render rows, but the Draft/star control there is not a `<button>` (find its
  element; likely a div with role or an svg wrapper).
- In the main table, before the user's turn the first control in a row is the star (queue);
  during the user's turn it becomes a "Draft" button. Clicking the star while on the clock
  drafts the player (happened at pick 22).
- `javascript_tool` calls time out at 45 s; in-page waits must be < 40 s. Loops keep running in
  the page after the tool returns.
- Queue panel lists queued players with their own Draft buttons when on the clock; autodraft
  takes from the queue in order.

## Shape

- `draft_cli.py recs --n 3` gives names + Yahoo ids. New: `--ids` flag printing `yahoo_id|name`
  lines for the script to consume.
- JS snippet (add to `.claude/skills/watch-draft/SKILL.md` as an optional step, or a new
  `queue-draft` skill): for each id, find `[data-id="<id>"]` in the table; if absent, search by
  surname, locate the row, click its star; skip anyone already queued or drafted; never click a
  "Draft" button (only star), so the step is safe to run while on the clock.
- Run it right after each tick when the user's pick is <= 3 away.

## Acceptance

In a mock: the queue shows the app's top 2-3 after every tick near the user's pick; deliberately
let one clock expire and confirm autodraft took the queued #1.

## Missing decision

Whether this is a separate skill or a step inside `/watch-draft` (which is documented as
read-only; queueing is a write to the room). Recommend a separate `/queue-draft` skill.
