# Live draft (2026-09-05): San Diego City Slackers, slot 11

12 teams, 15 rounds, 45 s clock, QB/RB2/WR3/TE/W-R/DEF/BN6, 0-PPR. Feed via `write_picks.py --append`
and `draft_cli.py tick`; fixture `test-data/draft-2026-09-05.json` (all 180 picks). Roughly half the
room autodrafted (SponsoredByChipotle!, Unsolicited Dak Pics, Mary Sold Seperately, Jaxon Sports
Network picked within seconds), so the room ran in bursts: 10 picks in ~90 s at the top, then
2-4 picks per 30-40 s read once humans were on the clock.

## Result

| pick | optimizer #1 / #2 / #3 | drafted | note |
|---|---|---|---|
| 11 | JSN / Henry / Achane (STRONG) | S. Barkley RB (#4) | user's call; JSN went at 12 |
| 14 | Henry / Achane / Allen | C. Lamb WR (#4) | Henry, Achane, Allen all survived to 17-26 |
| 35 | McBride TE / Skattebo / Rice (CLOSE) | McBride (#1) | |
| 38 | Rice / Skattebo / Swift | Rice (#1) | |
| 59 | Price / Hurts / Adams (STRONG) | Price (#1) | |
| 62 | Hurts / Maye / Daniels / Adams | Adams WR (#4) | user took WR3 over QB; QB run followed (66-69) |
| 83 | Dowdle / Prescott / Pollard | Prescott QB (#2) | |
| 86 | Dowdle / Pollard / Tate | Dowdle (#1) | |
| 107 | Dart / Texans / Broncos | Texans DEF (#2) | Dart gone at 106 |
| 110 | Nix / Kelce / Goedert | M. Lemon WR | user's upside pick, not in top 8 |
| 131 | Andrews / Mahomes / Meyers | Mahomes QB (#2) | |
| 134 | Andrews / H. Henry / Meyers | Meyers WR (#3) | |
| 155 | (TE-heavy list; user asked for upside RB/WR) | D. Wicks WR | queue could not be rebuilt: on the clock |
| 158 | Charbonnet / rookie WRs | Charbonnet RB (#1 of filtered list) | |
| 179 | Douglas / Bell / Cooper / Fields | Chargers DEF | user's call |

Final roster: QB Prescott (Mahomes); RB Barkley, Price, FLEX Dowdle (Charbonnet); WR Lamb, Rice,
Adams (Meyers, Lemon, Wicks); TE McBride; DEF Texans (Chargers).

## What worked

- **Tight loop, not cron.** One `javascript_tool` read that polls the Results table every 2-2.5 s
  for up to 30-40 s and returns as soon as N new rows appear, paired in the same turn with the Bash
  append of the previous read. The feed was never more than one pick behind the room, including
  the autodraft bursts.
- **Queue as the main control.** The user asked for the queue to be kept full at all times, not only
  at <= 3 away ("adding to the queue is the best way for this to work"). Refilling 8 targets after
  every tick, in rank order, meant every clock expiry would have taken the app's choice. The
  pre-draft queue of the top 14 (all of them gone by pick 11 except Barkley) is the right depth for
  a back-half slot.
- **Combined refill + read script.** Queue refill and the row read in one `javascript_tool` call
  (see `/queue-draft`), with a full clear-and-restar whenever the current queue order differs from
  the rank order. The order check is `cur.join() !== want.filter(id => cur.includes(id)).join()`.
- **Two-tab setup, not split view.** The user prefers two full-width tabs in the Claude tab group
  (Cmd+1 / Cmd+2). Chrome's split view moved the app tab out of the group; exiting split view
  dropped the whole group. Recovery: `tabs_context_mcp createIfEmpty:true`, open the app in the new
  group's tab, and have the user drag the existing draft tab into the group (never open a second
  draft-client tab).
- `draft_cli.py recs --n 30 --ids | grep -E '\|(RB|WR)$'` gives a position-filtered target list when
  the late-round ranking is dominated by bench TEs.

## What did not

- **Title states.** Yahoo also shows "You are next" (one pick away). The `/until your turn/` guard
  handles it, but scripts that key on "N picks until your turn" must treat "You are next" as 1 away.
- **On-clock queue rebuild is impossible.** At 155 the refill ran after the clock started and was
  correctly skipped, leaving the old TE-heavy queue. Rule: rebuild the queue the tick *before* the
  pick ahead, and when the user changes strategy, act on it at the next tick, not the next pick.
- **Late-round ranking prefers bench TEs.** From pick 131 on the top 8 was mostly TEs (Andrews,
  H. Henry, Ferguson, Strange, Schultz, Dulcich, Barner, Freiermuth) while the user wanted upside
  RB/WR and rookies. The bench penalty does not distinguish a third TE from a lottery-ticket WR.
  Open item: a bench-slot rule (no second bench TE while bench RB/WR are available) or a
  `--pos RB,WR` filter on `recs`.
- **`.ys-removequeue` readback is sometimes empty or stale** right after starring (rendering lag);
  a 250 ms wait per star is enough for the click but the final `queue:` line can miss the last
  entries. Verify with a screenshot when it matters.
- **The `tick.sh` helper** (append + status + targets JSON) lived in the session scratchpad; it should
  be a repo script (`draft_cli.py targets --n 8` printing the JS array) so it survives the session.
- Bash was blocked for one turn by an auto-mode classifier timeout mid-draft; the JS read still ran
  and the append caught up next turn. Keep reads and appends independent so one failing does not
  stall the other.
