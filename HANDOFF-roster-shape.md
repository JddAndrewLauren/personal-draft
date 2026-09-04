# Handoff: optimizer roster shape (WR starvation, bench-over-starter, DEF timing)

Written 2026-09-04 evening. Real draft **Sat 2026-09-05 11:30 PDT** (12 teams, QB/RB2/WR3/TE/
W-R flex/DEF/BN6, 0-PPR, 45 s clock). Branch `JddAndrewLauren/sync-main`, level with origin.

## Goal

Tune the recommendation so it fills starter slots before stacking bench depth, without breaking
the early-round picks that already grade well. Decide, with the user, how aggressive to be.

## Evidence (docs/mock-draft-2026-09-04b.md, findings 1-2; feed test-data/mock-draft-2026-09-04b.json, slot 3)

- Picks 46 and 51: with three WR starters empty the top four recs were all RBs (4th and 5th RB).
  First WR rec came at 70 (value 23); WR2 at 94 (value 10). Yahoo graded those RBs C.
- Pick 99: backup QB (VOR 10) recommended over the open WR3. Pick 118: bench TE with value -3
  over the open DEF slot. DEF never recommended before round 12.
- Root cause: `value` is VOR-based (`optimizer.compute_vor`, replacement levels from
  `replacement_ranks`), and in 0-PPR the RB replacement level sits far below WR, so every RB
  outranks every WR. The roster-need multipliers (`DEFAULT_CONFIG["need_multipliers"]`:
  starter_open 1.10, flex_open 1.00, bench 0.85, bench_deep 0.70) are too small to flip that,
  and `position_value_scale` halves DEF.

## Files

- optimizer.py: `recommend` (~449), `apply_multipliers`, `NeedInfo` / roster-need logic (~381),
  `DEFAULT_CONFIG` (~30-50), `compute_vor`, `replacement_ranks` (~69).
- config.yaml `optimizer:` block overrides the defaults per league.
- Harness: `replay_draft.py test-data/draft.json --all` (recorded league draft, slot 7) and
  `draft_cli.py recs --feed <fixture> --user-slot 3 --players test-data/players.csv` after
  truncating a fixture at a pick (see tests/test_draft_cli.py `feed_through`).

## Options to put to the user (pick one, or combine)

1. **Zero-starter boost**: when a position has *no* starter yet and open starter slots, multiply
   need by e.g. 1.5 instead of 1.10 (new key `starter_empty`). Smallest change.
2. **Bench cap while starters are open**: refuse bench picks at position X while any starter
   slot (incl. flex-eligible) is empty, unless the value gap exceeds a threshold.
3. **Flex-aware WR replacement**: compute WR replacement from starters + flex share (the
   `flex_weights` are RB 0.5 / WR 0.5; check they are applied to replacement ranks).
4. **DEF scale**: raise `position_value_scale.DEF` from 0.5 toward 0.8, or add a "fill DEF by
   round N" need boost, so DEF appears by round 12-13 of 15.

## Acceptance

- Replay both fixtures at their user slots: first WR rec by round 3-4 when WR starters are
  empty; no bench pick recommended while a starter slot is open unless the value gap is large;
  DEF recommended by round 13 at the latest.
- Early picks unchanged in `test-data/draft.json` replay (Taylor/Allen/Kraft-type calls).
- `pytest -q` green; add a test per rule in tests/test_optimizer.py.

## Missing decision

How aggressive: option 1 alone is safe; options 2-3 change the model's shape the day before the
draft. The user chooses.
