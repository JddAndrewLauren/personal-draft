# Mock draft 3 (2026-09-04c): roster-shape check + queue-loader rehearsal

Yahoo standard 12-team mock (30 s clock, K slot, W/R/T flex), slot 12, feed via `draft_cli.py tick`.
Policy: draft the optimizer's #1, fall back to #2/#3; keep the Yahoo Queue loaded with the top 3-4.
This mock folded plan items "Mock 3" (reshaped optimizer, commit 3bfa664) and "Mock 4" (`/queue-draft`,
commit d39427a) into one run. Feed fixture: `test-data/mock-draft-2026-09-04c.json`; raw log in the
session scratchpad. The room was all bots and ran at 4-13 s per pick, so timing numbers are invalid;
resolution, roster shape and DOM observations are valid.

## What happened, pick by pick

| pick | #1 / #2 / #3 (confidence) | drafted | how |
|---|---|---|---|
| 12 | (no tick: room opened during a 115 s wait) | K. Walker III RB (Yahoo autopick) | clock expired |
| 13 | (same) | J. Jefferson WR (Yahoo autopick) | clock expired -> **autopick mode on**; turned off |
| 36 | Price / Judkins / Montgomery (CLOSE, all RB; WR2, WR3 open) | Price (#1) | Draft button in the player row, script |
| 37 | (same queue) | Judkins (queued #1) | **deliberate expiry**: autodraft took the queue top -> autopick mode on again; turned off |
| 60 | Pierce / Watson / J. Williams (CLOSE, first WR rec) | Pierce (#1) | manual click on the Queue panel's Draft at 7 s left (row script failed, see below) |
| 61 | (back-to-back, queue empty) | D. Maye QB (Yahoo autopick) | clock expired while ticking -> autopick on; turned off |
| 84 | Dowdle / Metcalf / Sutton (CLOSE; bench RB over open WR3) | Dowdle (#1) | two-turn queue-panel script, <1 s |
| 85 | (same queue) | Metcalf (#2) | same script, turn 2, 7 s |
| 108 | Pitts TE / Sutton / Reed / Mason (recs 21 picks old) | T. Kelce TE | manual click at 13 s left; all four targets gone, queue empty |
| 109 | (queue empty) | B. Nix QB (Yahoo autopick) | clock expired -> autopick on; turned off |
| 132 | Monangai RB (bench) / Jets / Cardinals / Commanders DEF (MODERATE) | Jets DEF (#2; Monangai gone at 115) | two-turn script, 1 s |
| 133 | (same queue) | Cardinals DEF (#3) | same script, turn 2 (second DEF = wasted bench pick) |
| 156 | Andrews TE / Douglas / Ridley / Bateman (STRONG, all bench) | Douglas (#2; Andrews gone at 139) | two-turn script, 1 s |
| 157 | (same queue) | Ridley (#3) | same script, turn 2 |
| 180 | Otton / Waller / Helm / Bateman (CLOSE, LAST PICK) | Otton (#1) | script, queue reordered first (see below) |

Yahoo's post-draft grades: Maye A+, Jefferson A-, Kelce A+, Nix A+, Otton A, Douglas A-, Ridley A-;
Walker B+, Dowdle B+, Cardinals B+, Metcalf B-; Pierce C, Price C, Judkins C, Jets C.

## Roster-shape gates (plan "Mock 3")

1. **WR/TE at #1 by round 4-5 with WR starters open: pass, barely.** First WR #1 was pick 60 (round 5).
   At 36 (round 3) the top five were all RB with two WR starters open, because Jefferson (autopicked)
   already filled WR1, so `starter_empty` x1.5 did not apply and the plain starter-open x1.10 could not
   lift WR (value 32) over RB (value 36-41). Every league starter slot was filled by pick 132.
2. **DEF timing: pass.** DEF first reached #2 at 132 (round 11) behind a bench RB (Monangai, value 0.9 vs
   Jets -13.8: a 15-point gap, which the 5-point bench penalty is not meant to flip). On the frozen
   players file the same feed puts DEF #1 at 108 (round 9) and 132. No DEF or negative-value starter
   topped the list in rounds 6-8.
3. **TE: pass.** Pitts reached #1 at 108 (round 9) with the x1.50 "none rostered" boost; Kraft-type
   TEs went 69-82 to other teams without the app chasing them over WRs.
4. **Explanation bullets: pass.** "none rostered yet (need x1.50)" at pick 1 and 108; "-5 pts: a starter
   slot is still open" at 84 and 132.
5. **Early picks:** not comparable (12 and 13 were Yahoo autopicks).

Two rec-quality notes for a later session, not fixed here:

- **Bench RB over an open starter is still the norm in 0-PPR from round 7.** At 84 Dowdle (bench, value
  23) beat Metcalf (open WR3, value 10) by 13 after the -5; at 132 Monangai beat every DEF by 15. Yahoo
  graded those RB picks B+ and the WRs B-/C, so the optimizer may simply be right. Leave it.
- **Bullet inconsistencies.** At 108 Pitts showed "25% chance he is gone before your next pick" and
  "1% chance he reaches your pick #108" side by side; at 180 the table said `surv 0%` next to "100%
  chance he reaches your pick". The reach and survival numbers use different pick anchors and the
  last-pick case should not print a survival column.

## Queue-loader gates (plan "Mock 4")

6. **Queue within one tick: pass** once the guard bug was fixed (below). Every `/queue-draft` run
   starred all targets that were still on the board.
7. **`ON CLOCK - skipped`: pass, and it found a bug.** The first run said `ON CLOCK - skipped` while 12
   picks away, because the room title is "N picks until your turn" off the clock and the regex matched
   it. The guard now requires "your turn" without "until"; on the clock the title is
   "YOUR TURN, DRAFT NOW". It never drafted anything.
8. **Deliberate expiry: half pass.** With the queue loaded the expiry at 37 drafted the queued #1 (Judkins).
   But the "put into autopick mode" banner appeared after that single expiry, and again after 61 and 109:
   Yahoo re-arms autopick on *every* expiry once the team has been flagged, not after two. Turning it off
   costs one click on the Autodraft pill (about 2 s) plus dismissing the banner. Autopick mode with a loaded
   queue still drafts from the queue, so the real damage is only when the queue is empty.
9. **Selectors pinned: pass.** See "Verified DOM" in `.claude/skills/queue-draft/SKILL.md`.

## Loop, room and script findings

- **The room started before the lobby countdown ended** and moved at bot speed, so a 115 s wait cost
  picks 12 and 13. On draft day never leave the room unattended before your first pick; tick as soon
  as the draft-client tab exists.
- **Back-to-back picks (slots 1 and 12) need two picks' worth of queue.** Pick 61 expired with an empty
  queue while I was reading results after 60. From 84 on the queue held 4 targets and a two-turn script
  drafted both picks from the Queue panel. The second pick then follows the pre-first-pick recs, which
  produced a second DEF at 133; better is `recs --n 4` where the 2nd-4th are what the app would want
  *after* #1 is rostered (open question for the CLI).
- **Recs go stale fast.** Recs computed 21 picks before 108 lost all four targets (two went at 87-88).
  Re-tick and re-queue at <= 3 away even when the room is fast; if the pace is < 5 s/pick, re-queue
  once more at "You are next".
- **On your turn the DOM changes**: `ys-addqueue`/`ys-removequeue` vanish and rows get `Draft` buttons.
  Row-based lookup by `data-id` failed at 60; drafting the Queue panel's first `Draft` button is the
  reliable on-clock action. Script that worked (arm at <= 3 away, re-arm if it returns early):

  ```js
  const wait = ms => new Promise(s => setTimeout(s, ms));
  const txt = e => (e?.textContent || '').trim();
  const onClock = () => /your turn/i.test(document.title) && !/until your turn/i.test(document.title);
  const t0 = Date.now(); const left = () => 38000 - (Date.now() - t0);
  const queueDraft = () => {
    let el = [...document.querySelectorAll('div,span,p')].find(e => e.children.length === 0 && /Autodraft will pick from queue/i.test(txt(e)));
    for (let i = 0; i < 6 && el; i++) { el = el.parentElement; const b = [...el.querySelectorAll('button')].find(b => /^draft$/i.test(txt(b))); if (b) { b.click(); return 'queue Draft clicked (' + txt(b.parentElement?.parentElement).slice(0, 40) + ')'; } }
    return null;
  };
  const log = [];
  for (let turn = 1; turn <= 2 && left() > 0; turn++) {          // 2 turns for back-to-back picks
    while (!onClock() && left() > 0) await wait(200);
    if (!onClock()) { log.push('not on clock; ' + document.title.slice(0, 24)); break; }
    await wait(400); const r = queueDraft(); log.push('turn' + turn + ': ' + (r || 'NO queue Draft button')); if (!r) break;
    const t1 = Date.now(); while (onClock() && Date.now() - t1 < 4000) await wait(200);
    await wait(600);
  }
  log.join(' | ')
  ```

- **Queue order is star-time order**, not rank: Bateman, starred at 134, sat above the TEs starred at
  158 until I unstarred and re-starred him. The skill now unstars stale players and stars in rank order.
- **Search results include drafted players** (checkmark icon, no star); the skill reports them as
  `already drafted` instead of `not found`. A blank Yahoo id (Jets) resolved by name + position.
- **The Chrome extension disconnected once** ("Browser extension is not connected") between two arms;
  `tabs_context_mcp` reconnected on the next call, no tab lost.
- **watch-draft extractor unchanged**: `data-id` still on the Results rows, 180 picks read cleanly;
  pick 155 was cut by the ~1000-char output cap of a 20-row read (use MIN chunks of <= 20).
- **Sidebar settings**: feed toggle and team name stuck first try; the slot number input drops roughly
  half of rapid clicks during Streamlit reruns (set it with 2 s between clicks and zoom to verify).
- **Yahoo now sells "Instant Mock Drafts"** (Fantasy Plus) with a preset that mirrors the league
  (12 teams, 15 rounds, 45 s, no lobby). Not used; noted in case a paid rehearsal is ever wanted.
