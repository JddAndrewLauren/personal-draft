"""Draft-room scrape feed: picks read off the Yahoo draft page (by Claude in Chrome) -> DraftPicks.

Feed file (``scrape/picks.json``), rewritten as a full snapshot each tick::

    {"updated": 1757000000.0,
     "picks": [{"pick": 1, "team": "Team Gronk", "player": "Bijan Robinson",
                "position": "RB", "nfl_team": "ATL"}]}

Only ``pick``, ``player`` and ``position`` are required. ``yahoo_id`` (the draft room's
``data-id``) resolves the player exactly, so abbreviated names like "J. Gibbs" still match. ``team`` (the fantasy team name) drives
slot inference from round 1; ``nfl_team`` disambiguates players with the same name/position.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from models import DraftPick, resolve_player, snake_slot_for_pick


def load_picks(path) -> dict:
    """Read the feed; a missing or empty file yields ``{"updated": 0, "picks": []}``."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {"updated": 0.0, "picks": []}
    d = json.loads(p.read_text())
    rows = []
    for r in d.get("picks", []):
        if not r.get("player"):
            continue
        rows.append({
            "pick": int(r["pick"]),
            "team": (r.get("team") or "").strip(),
            "player": str(r["player"]).strip(),
            "position": (r.get("position") or "").strip(),
            "nfl_team": (r.get("nfl_team") or "").strip(),
            "yahoo_id": str(r.get("yahoo_id") or "").strip(),
        })
    rows.sort(key=lambda r: r["pick"])
    return {"updated": float(d.get("updated") or 0.0), "picks": rows}


def write_picks(rows: list, path, updated: Optional[float] = None) -> None:
    """Atomically write a feed snapshot."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"updated": updated or time.time(), "picks": rows}, indent=1))
    os.replace(tmp, p)


def assign_slots_from_names(rows: list, num_teams: int) -> dict:
    """Infer each fantasy team's snake slot from round-1 picks; returns {team_name: slot}."""
    return {r["team"]: r["pick"] for r in rows if r["team"] and r["pick"] <= num_teams}


def draft_picks_from_scrape(rows: list, players: list, num_teams: int, slots: Optional[dict] = None) -> list:
    """Translate feed rows into DraftPick objects.

    Unresolved names get the placeholder id ``scrape:<name>`` so the pick is still recorded.
    """
    slots = slots or {}
    by_yid = {p.yahoo_player_id: p for p in players if p.yahoo_player_id}
    out = []
    for r in rows:
        pl = by_yid.get(r.get("yahoo_id") or "") or resolve_player(players, r["player"], r["position"], r["nfl_team"] or None)
        slot = slots.get(r["team"]) or snake_slot_for_pick(r["pick"], num_teams)
        out.append(DraftPick(
            pick=r["pick"], round=(r["pick"] - 1) // num_teams + 1, slot=slot,
            player_id=pl.player_id if pl else f"scrape:{r['player']}",
            source="scrape", confirmed=True,
            team_key=r["team"] or None,
            player_name=pl.name if pl else r["player"],
        ))
    return out
