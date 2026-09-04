#!/usr/bin/env python3
"""Text interface to the optimizer over the draft-room feed (for Claude Code during the draft).

    python draft_cli.py [--feed scrape/picks.json] [--team "Your Team"] [--user-slot N] status
    python draft_cli.py recs [--n 5]
    python draft_cli.py tick [--n 5] < picks.txt      # append feed lines (write_picks format), then status + recs

Read-only over the feed: it never writes draft_state.json (the Streamlit app keeps its own
session state). The feed is the single source of truth; the app stays the human's display.
The user's slot comes from the round-1 pick made by ``--team`` (Yahoo's draft room lists the
user's team as "Your Team"), else ``--user-slot``, else config ``draft.user_slot``.
"""
from __future__ import annotations

import argparse
import sys
import time

import scrape as sc
from models import DraftState, default_teams, load_config, load_players, settings_from_config
from optimizer import assign_roster_slots, merge_config, prepare_players, recommend
from write_picks import parse_lines

MY_TEAM = "Your Team"


def state_from_feed(feed_path, cfg: dict, players: list, team: str = MY_TEAM, user_slot=None):
    """Fresh DraftState built from the feed snapshot. Returns (state, feed, unresolved_rows)."""
    settings = settings_from_config(cfg)
    ocfg = merge_config(cfg.get("optimizer"))
    prepare_players(players, settings, ocfg)
    feed = sc.load_picks(feed_path)
    rows = feed["picks"]
    slots = sc.assign_slots_from_names(rows, settings.num_teams)
    slot = slots.get(team) or int(user_slot or cfg.get("draft", {}).get("user_slot", 1))
    state = DraftState(settings=settings, teams=default_teams(settings.num_teams, slot), user_slot=slot)
    for name, s in slots.items():
        t = state.team_for_slot(s)
        if t is not None:
            t.name = name
    picks = sc.draft_picks_from_scrape(rows, players, settings.num_teams, slots)
    state.merge_yahoo(picks, source="scrape")
    unresolved = [p for p in picks if p.player_id.startswith("scrape:")]
    return state, feed, unresolved, ocfg


def status_text(state: DraftState, feed: dict, unresolved: list, players: list, team: str) -> str:
    by_id = {p.player_id: p for p in players}
    age = f"{time.time() - feed['updated']:.0f}s ago" if feed["updated"] else "never"
    lines = [f"feed: {len(feed['picks'])} picks, updated {age} | slot {state.user_slot} ({team})"]
    if state.is_complete:
        lines.append("draft complete")
    else:
        away = state.picks_until_user
        clock = "YOU ARE ON THE CLOCK" if state.on_the_clock else f"{away} picks away (your next: #{state.next_user_pick()})"
        lines.append(f"pick {state.current_pick} / round {state.current_round} | on the clock: "
                     f"{state.team_name(state.slot_for_pick(state.current_pick))} | {clock}")
    mine = [by_id[pid] for pid in state.user_roster_ids() if pid in by_id]
    unknown = [p.player_name for p in state.picks if p.slot == state.user_slot and p.player_id not in by_id]
    slots = assign_roster_slots(mine, state.settings.roster)
    parts = [f"{k}: {', '.join(p.name for p in v)}" for k, v in slots.items() if v]
    if unknown:
        parts.append(f"?: {', '.join(unknown)}")
    lines.append(f"roster ({len(mine) + len(unknown)}/{state.settings.roster.total_slots}): " + ("; ".join(parts) or "empty"))
    if unresolved:
        lines.append("unresolved: " + ", ".join(f"#{p.pick} {p.player_name}" for p in unresolved))
    if state.conflicts:
        lines.append(f"CONFLICTS: {len(state.conflicts)}")
    return "\n".join(lines)


def recs_text(state: DraftState, players: list, ocfg: dict, n: int = 5) -> str:
    if state.is_complete or state.next_user_pick() is None:
        return "no more picks for you"
    t = time.perf_counter()
    recs = recommend(state, players, ocfg)
    ms = (time.perf_counter() - t) * 1000
    out = [f"recs for your pick #{state.next_user_pick()} (following: {state.following_user_pick()})  [{ms:.0f} ms]"]
    for i, r in enumerate(recs[:n], start=1):
        p = r.player
        adp = f"{p.adp:.0f}" if p.adp is not None else "-"
        out.append(f"  {i}. {p.name:24} {p.position:3} {p.team:3} score {r.adjusted_score:6.1f} value {r.value:6.1f} "
                   f"adp {adp:>4} surv {r.survival:4.0%} wait {r.wait_cost:5.1f} need x{r.roster_need:.2f} "
                   f"{p.risk_label:9} {r.action}")
    if recs:
        out.append(f"  confidence: {recs[0].confidence}")
        out.extend(f"    - {b}" for b in recs[0].reasons)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "recs", "tick"])
    ap.add_argument("--feed", default="scrape/picks.json")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--players", default=None)
    ap.add_argument("--team", default=MY_TEAM)
    ap.add_argument("--user-slot", type=int, default=None)
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args(argv)
    if a.command == "tick":
        rows = parse_lines(sys.stdin.read())
        merged = {r["pick"]: r for r in sc.load_picks(a.feed)["picks"]}
        merged.update({r["pick"]: r for r in rows})
        sc.write_picks([merged[k] for k in sorted(merged)], a.feed)
        print(f"appended {len(rows)} picks")
    cfg = load_config(a.config)
    players = load_players(a.players or cfg["paths"]["players_csv"])
    state, feed, unresolved, ocfg = state_from_feed(a.feed, cfg, players, a.team, a.user_slot)
    if a.command in ("status", "tick"):
        print(status_text(state, feed, unresolved, players, a.team))
    if a.command in ("recs", "tick"):
        print(recs_text(state, players, ocfg, a.n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
