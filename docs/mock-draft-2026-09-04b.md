# Mock draft 2 (2026-09-04): Claude drafts, following the optimizer's #1

Yahoo standard 12-team mock (30 s clock, K slot), slot 3, feed via `draft_cli.py tick`.
Policy: draft the optimizer's #1 every time; fall back to #2/#3 if #1 is gone. Feed fixture:
`test-data/mock-draft-2026-09-04b.json`; raw log in the session scratchpad.

## What happened, pick by pick

| pick | #1 / #2 / #3 (confidence) | drafted | how |
|---|---|---|---|
| 3 | Gibbs / B. Robinson / J. Taylor (STRONG) | J. Taylor (#3; 1-2 gone) | queue pre-loaded, Draft from Queue panel, ~17 s |
| 22 | Javonte Williams / B. Hall / J. Love (STRONG) | Javonte Williams (#1) | script, ~5 s |
| 27 | Josh Allen / G. Pickens / B. Hall (CLOSE) | Josh Allen (#1) | script, <1 s |
| 46 | Jadarian Price / Skattebo / Judkins (CLOSE, all RB) | Price (#1) | script, <1 s |
| 51 | Judkins / Montgomery / Irving (CLOSE, all RB) | Judkins (#1) | script + "I drafted him" test |
| 70 | C. Watson / A. Pierce / J. Warren (CLOSE) | Pierce (#2; Watson gone at 66) | script fallback |
| 75 | T. Kraft / R. Dowdle / DK Metcalf (CLOSE) | Kraft (#1) | script, <1 s |
| 94 | DK Metcalf / C. Sutton / B. Corum (MODERATE) | Sutton (#2; Metcalf gone at 92) | script fallback |
| 99 | J. Dart / B. Nix / K. Monangai (MODERATE) | autodraft: J. Addison WR | clock expired (tick + script > 30 s) |
| 118 | Kelce / Goedert / Andrews (CLOSE, all TE, value < 0) | autodraft: R. White RB | room at ~3 s/pick, fallbacks gone |
| 123-171 | (no tick possible) | autopick: K. Murray, H. Henry, X. Worthy, J. Myers K, Steelers DEF | Yahoo autopick mode |

Yahoo's post-draft grades: Taylor A+, Allen A, Kraft A+, Murray A, Myers A+, Steelers A+;
Javonte Williams C, Price C, Pierce C, Sutton C-, White C, Judkins B-, Addison D+.

## Optimizer findings

1. **It builds an RB-heavy roster and starves WR.** With three WR starters empty it recommended
   a 4th RB at 46 (top four all RB) and a 5th at 51. The first WR recommendation came at 70
   with value 23; WR2 at 94 had value 10. Root cause: value is VOR-based and in 0-PPR the RB
   replacement level is far below WR, so RBs always carry more VOR; the roster-need multiplier
   (starter open ×1.10 vs bench ×0.85/×0.70) is not large enough to flip it. Yahoo graded those
   mid-round RBs C. Candidate fixes: a stronger starter-open multiplier for a position with
   *zero* starters, or a per-position cap on bench picks while a starter slot is empty.
2. **Bench players outrank open starters late.** At 99 it recommended a backup QB (VOR 10) over
   the open WR3; at 118 a bench TE with *negative* value over the open DEF slot. DEF value is
   scaled ×0.5, so DEF is never recommended before round 12.
3. **"TAKE NOW" is attached to the #1 whenever confidence isn't CLOSE**, even when the reasons
   say "little urgency" (22, 46) or the player has a 15% chance of reaching the pick (142).
   `_action` should read survival/wait cost, not just rank.
4. **Reach estimates missed twice in a row**: Watson "76% chance he reaches #70" (gone at 66),
   Metcalf "66% reaches #94" (gone at 92). Small sample, but both were ADP-late players in a
   room that drafts faster than ADP. The "NOW OR NEVER" calls (Pickens, Montgomery, Irving) were
   all correct.
5. Early picks were sound: Taylor at 3, Allen at 27 (last Tier-1 QB), Kraft at 75 (last Tier-1
   TE) all graded A/A+.

## App and loop findings

- **CLI matched the app's #1 at every checked pick** (3, 22, 27, 46, 51). One Bash call per tick
  replaced screenshot + zoom; per-pick tool calls dropped from ~7 to ~3.
- **Manual path works when the feed is current**: "I drafted him" at 51, then feed 51 arrived ->
  `source=manual, confirmed=True`, no conflict. The confirmation was persisted only because new
  picks arrived in the same merge; fixed in this commit (save on any confirmation).
- **Sidebar settings did not stick on the first attempt**: toggle needed three clicks (two ref
  clicks, then a coordinate click); team name needed a retry. Set them before joining.
- **Stale-feed badge fires at 120 s**; ticking every 2 min trips it.
- **Yahoo autopick mode**: after two expired clocks (99, 118) the room switched my team to
  autopick and, with every team autopicking, finished picks 126-180 in about a minute. This is
  the biggest draft-day risk: two misses and the rest of the draft is gone. Keep the queue
  loaded with the top 2-3 recs at all times; if the banner appears, turn autopick off.
- **Room mechanics**: the Players table only lists the top ~100 by rank, so late-round targets
  (TE3, DEF, K) are not in the DOM; the search box works (React input needs the native value
  setter + `input` event). The Draft control in a search-result row is not a `<button>`.
  `javascript_tool` calls time out at 45 s, so in-page waits must be shorter than ~40 s.
- Under a 30 s clock the reliable sequence is: tick and read recs *before* the pick ahead of
  mine, then one script that waits for "YOUR TURN" in the title and clicks Draft with #1/#2/#3
  fallbacks. Doing tick + script after the clock started (pick 99) is too slow.
