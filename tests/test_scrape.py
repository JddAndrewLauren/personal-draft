import json
from pathlib import Path

from models import DraftState, default_teams, load_config, load_players, settings_from_config
import scrape as sc
from write_picks import parse_lines

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "test-data" / "scrape" / "picks.json"


def make_state(user_slot=7):
    cfg = load_config(ROOT / "config.yaml")
    settings = settings_from_config(cfg)
    return DraftState(settings=settings, teams=default_teams(settings.num_teams, user_slot), user_slot=user_slot)


def players():
    return load_players(ROOT / "data" / "players.csv")


def test_load_picks_missing_file(tmp_path):
    assert sc.load_picks(tmp_path / "nope.json") == {"updated": 0.0, "picks": []}


def test_slots_from_round_one():
    feed = sc.load_picks(FEED)
    slots = sc.assign_slots_from_names(feed["picks"], 12)
    assert slots["Squad 1"] == 1 and slots["Squad 12"] == 12 and len(slots) == 12


def test_draft_picks_resolve_and_placeholder():
    feed = sc.load_picks(FEED)
    picks = sc.draft_picks_from_scrape(feed["picks"], players(), 12, sc.assign_slots_from_names(feed["picks"], 12))
    assert picks[0].player_id == "bijan robinson|RB"
    assert picks[0].slot == 1 and picks[0].source == "scrape" and picks[0].confirmed
    assert picks[12].pick == 13 and picks[12].slot == 12          # snake: round 2 starts at slot 12
    last = picks[-1]
    assert last.player_id == "scrape:Nobody Realname" and last.player_name == "Nobody Realname"


def test_merge_is_idempotent_and_confirms_manual():
    st = make_state()
    feed = sc.load_picks(FEED)
    st.add_pick("bijan robinson|RB", pick=1, player_name="Bijan Robinson")   # manual pick, unconfirmed
    picks = sc.draft_picks_from_scrape(feed["picks"], players(), 12)
    new, conflicts = st.merge_yahoo(picks, source="scrape")
    assert len(new) == len(picks) - 1 and not conflicts
    assert st.pick_by_number(1).confirmed and st.pick_by_number(1).source == "manual"
    assert st.pick_by_number(2).source == "scrape"
    again, conflicts = st.merge_yahoo(picks, source="scrape")
    assert not again and not conflicts


def test_conflict_carries_source_and_resolves_to_scrape():
    st = make_state()
    st.add_pick("justin jefferson|WR", pick=1, player_name="Justin Jefferson")
    feed = sc.load_picks(FEED)
    picks = sc.draft_picks_from_scrape(feed["picks"][:1], players(), 12)
    _, conflicts = st.merge_yahoo(picks, source="scrape")
    assert len(conflicts) == 1 and conflicts[0].source == "scrape"
    st.resolve_conflict(1, keep="yahoo")
    p = st.pick_by_number(1)
    assert p.player_id == "bijan robinson|RB" and p.source == "scrape"
    d = json.loads(json.dumps(st.to_dict()))
    assert DraftState.from_dict(d).picks[0].source == "scrape"


def test_write_picks_roundtrip(tmp_path):
    rows = parse_lines("# header\n2 | Squad 2 | Ja'Marr Chase | WR | CIN\n1|Squad 1|Bijan Robinson|RB\n")
    path = tmp_path / "scrape" / "picks.json"
    sc.write_picks(rows, path)
    feed = sc.load_picks(path)
    assert [r["pick"] for r in feed["picks"]] == [1, 2]
    assert feed["picks"][0]["nfl_team"] == "" and feed["picks"][1]["nfl_team"] == "CIN"
    assert feed["updated"] > 0


def test_settings_from_config_reads_league_scoring_and_no_kicker():
    cfg = load_config("config.yaml")
    s = settings_from_config(cfg)
    assert s.scoring["11"] == 0.0 and s.scoring["18"] == -2.0
    assert "K" not in s.roster.starter_positions
    assert s.roster.flex_positions == ("RB", "WR")
    assert s.rounds == 15 and s.roster.total_slots == 15


def test_yahoo_id_resolves_abbreviated_names_and_tolerates_kickers(tmp_path):
    """The draft room abbreviates names ("J. Gibbs") but exposes Yahoo's player id; a K pick
    by another team (no K rows in players.csv) must land as a placeholder, not crash."""
    rows = parse_lines("1 | Your Team | J. Gibbs | RB | Det | 40059\n"
                       "2 | Ahil | B. Aubrey | K | Dal | 34321\n"
                       "3 | Miguel | Texans D/ST | DEF | Hou |\n")
    assert rows[0]["yahoo_id"] == "40059" and rows[2]["yahoo_id"] == ""
    path = tmp_path / "picks.json"
    sc.write_picks(rows, path)
    feed = sc.load_picks(path)
    picks = sc.draft_picks_from_scrape(feed["picks"], players(), 12, sc.assign_slots_from_names(feed["picks"], 12))
    assert picks[0].player_id == "jahmyr gibbs|RB"
    assert picks[1].player_id == "scrape:B. Aubrey" and picks[1].slot == 2
    assert picks[2].player_id == "texans|DEF"
    state = make_state(user_slot=1)
    new, conflicts = state.merge_yahoo(picks, source="scrape")
    assert len(new) == 3 and not conflicts


def test_write_picks_append_merges_by_pick(tmp_path):
    from write_picks import main
    import io, sys
    path = tmp_path / "picks.json"
    sc.write_picks(parse_lines("1 | A | Jahmyr Gibbs | RB | DET\n2 | B | Bijan Robinson | RB | ATL"), path)
    sys.stdin = io.StringIO("2 | B | Bijan Robinson | RB | ATL | 40055\n3 | C | Puka Nacua | WR | LAR | 40168")
    try:
        main(["--path", str(path), "--append"])
    finally:
        sys.stdin = sys.__stdin__
    feed = sc.load_picks(path)
    assert [r["pick"] for r in feed["picks"]] == [1, 2, 3]
    assert feed["picks"][1]["yahoo_id"] == "40055"


def test_full_mock_draft_feed_resolves_everything_but_kickers():
    """Real feed from the 2026-09-04 Yahoo mock (180 picks, id-first resolution)."""
    feed = sc.load_picks(ROOT / "test-data" / "mock-draft-2026-09-04.json")
    picks = sc.draft_picks_from_scrape(feed["picks"], players(), 12, sc.assign_slots_from_names(feed["picks"], 12))
    assert len(picks) == 180 and picks[-1].pick == 180
    unresolved = [r for r, p in zip(feed["picks"], picks) if p.player_id.startswith("scrape:")]
    assert unresolved and all(r["position"] == "K" for r in unresolved)
    assert sum(1 for p in picks if p.player_id.endswith("|DEF")) == 12
    assert [p.player_name for p in picks if p.slot == 1][:3] == ["Jahmyr Gibbs", "A.J. Brown", "Chris Olave"]
