# Plan: further mock drafts before the real draft

Real draft: **Sat 2026-09-05 11:30 PDT**, San Diego City Slackers, 12 teams, 15 rounds,
QB/RB2/WR3/TE/W-R flex/DEF/BN6, 0-PPR, 45 s clock. Two mocks done on 2026-09-04
(`docs/mock-draft-2026-09-04b.md`). Each further mock costs about 35-45 minutes of wall clock
and should answer a specific question; do not run one without a purpose from the list below.

## What a mock can and cannot tell us

Yahoo's standard mock rooms differ from the league: 30 s clock, a K slot, W/R/T flex, and
rooms that collapse into autodraft speed once a few humans leave. They are a good test of the
scrape loop, the pick protocol and the optimizer's roster shape, and a poor test of pacing
(the real room will be slower and more human). Treat autodraft-speed stretches as invalid for
timing measurements but still valid for resolution and roster-shape observations.

## Mock 3: verify the roster-shape fix (run after HANDOFF-roster-shape.md lands)

Question: with the tuned need multipliers, does the optimizer fill WR starters by round 3-4
and DEF by round 13, without changing the early picks that graded A?

- Policy: follow #1 every time (same as mock 2) so the comparison is clean.
- Compare against mock 2 at the same pick numbers: first WR rec, first DEF rec, number of
  bench picks recommended while a starter was open, Yahoo grades for the mid-round picks.
- Pass: first WR rec <= round 4 when WR starters are empty; no bench rec while a starter slot
  is open unless the value gap is > 15; DEF rec by round 13; Taylor/Allen/Kraft-type calls
  unchanged in the `test-data/draft.json` replay.

## Mock 4: queue loader and the two-miss risk (run after HANDOFF-queue-loader.md lands)

Question: with the queue kept loaded, does an expired clock take the app's choice, and does
the team ever enter autopick mode?

- Deliberately let one clock expire mid-draft and confirm autodraft took the queued #1.
- Confirm the "put into autopick mode" banner never appears; if it does, practise turning it
  off and note how many seconds that costs.
- Measure queue-load latency per tick (target: queue updated within one tick of picks-away
  reaching 3).
- Sequence: `/watch-draft` tick -> when 3 or fewer away, `/queue-draft` (first run: do its
  read-only DOM probe and pin the star/queue selectors in the skill) -> arm the YOUR TURN
  script. Also invoke `/queue-draft` once while on the clock to confirm it answers
  `ON CLOCK - skipped` and drafts nothing.

**Result (2026-09-04c, `docs/mock-draft-2026-09-04c.md`): Mock 3 and Mock 4 were run as one mock.**
Roster-shape gates pass (first WR #1 at round 5, DEF by round 11, TE at round 9, every starter filled).
Queue fallback works: an expiry with a loaded queue drafted the app's #1. Caveats: Yahoo re-arms
autopick mode after *every* expiry once flagged (one click to clear); back-to-back picks need 4 queued
targets and the two-turn script; recs older than ~10 picks go stale. The stop rule below is met.

## Mock 5 (optional): human in the loop

Question: is the app readable enough for the user to act on in 45 s without the CLI?

- The user drafts in the room and reads only the Streamlit app; Claude runs `/loop 30s
  /watch-draft` and nothing else. Claude does not make picks.
- Record, per user pick: seconds from clock start to click, whether the pick was the app's #1,
  and any moment the user needed information the app did not show.
- This is the closest rehearsal of Saturday and the only one that tests the human path.

## Pre-flight (every mock, in this order, before joining a room)

1. `git status` clean; `.venv/bin/python -m pytest -q` green.
2. `rm -f draft_state.json`; `printf '' | .venv/bin/python write_picks.py`.
3. `.venv/bin/streamlit run app.py --server.headless true` in the background; Readiness page
   9/9 green.
4. Sidebar: Pick source > Draft room > **Watch scrape feed** on; team name **Your Team**;
   poll 5 s. Verify both stuck (zoom the sidebar) before joining; both have failed silently.
5. `draft_cli.py status` shows pick 1, empty roster, no conflicts.
6. Chrome tools loaded in one ToolSearch; a fresh tab for the lobby.
7. Log file with the row template from mock 2 (pick, round, #1/#2/#3, confidence, action,
   drafted, seconds, feed confirmed, notes).
8. Only then click **12 Team** in the lobby (that click joins immediately; no confirm).

## Pick protocol (Claude drafting)

- Tick every ~30 s normally; every ~10 s when picks-away <= 3.
- With picks-away <= 3: tick, then `/queue-draft` (3 targets, 4 when the next two picks are
  back-to-back), then arm the two-turn Queue-panel script from `docs/mock-draft-2026-09-04c.md`
  ("Loop, room and script findings"). On your turn the row star controls vanish; the Queue panel's
  Draft button is the reliable control. In-page waits must stay under 40 s; re-arm if the tool
  returns before the turn.
- Tick as soon as the draft-client tab exists: mock rooms start before the lobby countdown ends.
- If the "autopick mode" banner appears, click the Autodraft pill in the Queue header once (2 s).
- Never run tick + script after the clock has started; that sequence lost pick 99.
- If the room collapses to autodraft speed (< 5 s per pick), stop trying to pick and keep the
  queue loaded; log the stretch as invalid for timing.

## Observations to record every time

Optimizer: #1 by pick and whether it was taken; confidence and action labels; first WR/TE/
DEF rec rounds; bench-vs-starter recs; reach-estimate hit rate for "likely available" calls;
Yahoo grades afterwards.
Loop: CLI #1 vs app #1 at each user pick; conflicts (expect none); stale-feed badges; manual
"I drafted him" confirmations if exercised; seconds from clock start to click.
Room: anything new in the DOM (labels, badges, row shapes) that broke the extractor.

## After each mock

1. Save the feed as `test-data/mock-draft-<date><letter>.json` and add the standard fixture test
   (all 180 picks, only kickers unresolved, first three user picks pinned).
2. Extend `test-data/players.csv` with any players the fixture needs.
3. Findings doc under `docs/`; fix only what broke live, keep the optimizer changes to the
   handoff sessions.
4. Reset state, stop Streamlit, close the room tab, commit, push.

## Stop rule

Stop running mocks when mock 3 passes its roster-shape gate and mock 4 shows the queue
fallback working. Mock 5 is worth it only if there is a spare hour on Friday evening; the
user's own rehearsal matters more than a third agent-driven run.
