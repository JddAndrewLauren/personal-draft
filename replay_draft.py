#!/usr/bin/env python3
"""Replay a recorded draft pick by pick and print the optimizer's view at every user pick.

    python replay_draft.py test-data/draft.json [--players data/players.csv] [--user-slot 7] [--all]

Draft JSON format:
    {"teams": 12, "rounds": 15, "user_slot": 7,
     "roster": {"QB": 1, ...},            # optional, defaults to config.yaml
     "picks": [{"pick": 1, "player": "Bijan Robinson", "position": "RB", "team": "ATL"}, ...]}

Also usable as a sanity harness: it reports how often the recorded user pick matched the
optimizer's #1 / top-3 and the maximum recompute time (target < 500 ms).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from models import (
    DraftState,
    RosterConfig,
    default_teams,
    load_config,
    load_players,
    resolve_player,
    settings_from_config,
)
from optimizer import merge_config, prepare_players, recommend


def load_draft(path) -> dict:
    return json.loads(Path(path).read_text())


def resolve(players, name, position, team=None) -> object:
    return resolve_player(players, name, position, team)


def replay(draft: dict, players: list, cfg: dict, user_slot=None, show_all=False, out=sys.stdout) -> dict:
    settings = settings_from_config(cfg)
    settings.num_teams = int(draft.get("teams", settings.num_teams))
    settings.rounds = int(draft.get("rounds", settings.rounds))
    if draft.get("roster"):
        settings.roster = RosterConfig(slots={k: int(v) for k, v in draft["roster"].items()},
                                       flex_positions=tuple(draft.get("flex_positions", ("RB", "WR", "TE"))))
    user_slot = int(user_slot or draft.get("user_slot", cfg["draft"].get("user_slot", 1)))
    ocfg = merge_config(cfg.get("optimizer"))
    prepare_players(players, settings, ocfg)
    state = DraftState(settings=settings, teams=default_teams(settings.num_teams, user_slot), user_slot=user_slot)

    stats = {"user_picks": 0, "top1": 0, "top3": 0, "max_ms": 0.0, "unresolved": []}
    picks = sorted(draft["picks"], key=lambda p: p["pick"])
    for rec in picks:
        if state.on_the_clock or show_all:
            t = time.perf_counter()
            recs = recommend(state, players, ocfg)
            ms = (time.perf_counter() - t) * 1000
            stats["max_ms"] = max(stats["max_ms"], ms)
            if state.on_the_clock:
                stats["user_picks"] += 1
                chosen = resolve(players, rec["player"], rec.get("position", ""))
                names = [r.player.player_id for r in recs[:3]]
                if chosen and names and names[0] == chosen.player_id:
                    stats["top1"] += 1
                if chosen and chosen.player_id in names:
                    stats["top3"] += 1
                print(f"\n=== Round {state.current_round}  Pick {state.current_pick}  (you)  "
                      f"next: {state.following_user_pick()}  [{ms:.0f} ms]", file=out)
                print(f"    recorded pick: {rec['player']} ({rec.get('position', '?')})", file=out)
                for i, r in enumerate(recs[:5], start=1):
                    p = r.player
                    print(f"  {i}. {p.name:24} {p.position:3} score {r.adjusted_score:6.1f}  value {r.value:6.1f}  "
                          f"surv {r.survival:4.0%}  wait {r.wait_cost:5.1f}  need x{r.roster_need:.2f}  "
                          f"{p.risk_label:9} {r.action}", file=out)
                if recs:
                    print(f"     confidence: {recs[0].confidence}", file=out)
                    for b in recs[0].reasons:
                        print(f"       - {b}", file=out)
        player = resolve(players, rec["player"], rec.get("position", ""))
        if player is None:
            stats["unresolved"].append(rec["player"])
            pid = "unknown:" + rec["player"]
        else:
            pid = player.player_id
        state.add_pick(pid, pick=int(rec["pick"]), player_name=rec["player"])

    print(f"\nDone: {state.current_pick - 1} picks replayed. User picks: {stats['user_picks']}; "
          f"recorded pick was optimizer #1 {stats['top1']}x, top-3 {stats['top3']}x. "
          f"Max recompute {stats['max_ms']:.0f} ms.", file=out)
    if stats["unresolved"]:
        print(f"Unresolved names ({len(stats['unresolved'])}): {', '.join(stats['unresolved'][:10])}", file=out)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", help="recorded draft JSON")
    ap.add_argument("--players", default=None, help="players CSV (default: config paths.players_csv)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--user-slot", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="recompute after every pick (timing check)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    players = load_players(args.players or cfg["paths"]["players_csv"])
    replay(load_draft(args.draft), players, cfg, user_slot=args.user_slot, show_all=args.all)


if __name__ == "__main__":
    main()
