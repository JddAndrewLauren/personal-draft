"""draft_cli: status / recs / tick over the scrape feed (no draft_state.json involved)."""
import io
import json
import sys
from pathlib import Path

import draft_cli
from models import load_config, load_players

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "test-data" / "mock-draft-2026-09-04.json"
PLAYERS = ROOT / "test-data" / "players.csv"


def feed_through(tmp_path, last_pick: int) -> Path:
    d = json.loads(FULL.read_text())
    d["picks"] = [r for r in d["picks"] if r["pick"] <= last_pick]
    p = tmp_path / "picks.json"
    p.write_text(json.dumps(d))
    return p


def run(argv, stdin: str = "") -> str:
    buf, old_out, old_in = io.StringIO(), sys.stdout, sys.stdin
    sys.stdout, sys.stdin = buf, io.StringIO(stdin)
    try:
        draft_cli.main(argv + ["--config", str(ROOT / "config.yaml"), "--players", str(PLAYERS)])
    finally:
        sys.stdout, sys.stdin = old_out, old_in
    return buf.getvalue()


def test_state_from_feed_infers_slot_from_your_team(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    state, feed, unresolved, _ = draft_cli.state_from_feed(feed_through(tmp_path, 23), cfg, load_players(PLAYERS))
    assert state.user_slot == 1 and state.on_the_clock and state.current_pick == 24
    assert state.user_roster_ids() == ["jahmyr gibbs|RB"] and not unresolved and not state.conflicts


def test_status_and_recs_on_the_clock(tmp_path):
    out = run(["status", "--feed", str(feed_through(tmp_path, 23))])
    assert "YOU ARE ON THE CLOCK" in out and "slot 1 (Your Team)" in out and "RB: Jahmyr Gibbs" in out
    out = run(["recs", "--feed", str(feed_through(tmp_path, 23)), "--n", "5"])
    lines = [l for l in out.splitlines() if l.strip()[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(lines) == 5 and "confidence:" in out
    assert not any(" K " in l for l in lines)            # no kicker rows exist to recommend
    assert "1. " in lines[0] and any(a in lines[0] for a in ("TAKE NOW", "SAFE TO WAIT", "CLOSE DECISION"))


def test_tick_appends_and_advances(tmp_path):
    feed = feed_through(tmp_path, 23)
    out = run(["tick", "--feed", str(feed)],
              stdin="24 | Your Team | A. Brown | WR | NE | 31883\n25 | Your Team | C. Olave | WR | NO | 33966\n")
    assert "appended 2 picks" in out and "pick 26" in out and "WR: A.J. Brown, Chris Olave" in out
    assert "recs for your pick #48" in out


def test_kicker_placeholder_and_complete_draft():
    out = run(["status", "--feed", str(FULL)])
    assert "draft complete" in out and "?: C. Little" in out and "unresolved:" in out
    assert "no more picks" in run(["recs", "--feed", str(FULL)])
