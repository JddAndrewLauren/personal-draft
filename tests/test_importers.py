import io
from pathlib import Path

import pytest

from models import (
    FORMAT_FP_ECR,
    FORMAT_FP_PROJECTIONS,
    FORMAT_GENERIC,
    LeagueSettings,
    Player,
    detect_format,
    load_players,
    merge_external,
    normalize_name,
    normalize_position,
    normalize_team,
    points_from_stats,
    read_external,
    load_mappings,
    save_mappings,
    apply_mappings,
)
from optimizer import prepare_players

DATA = Path(__file__).resolve().parent.parent / "test-data"


def test_normalisation():
    assert normalize_name("D.J. Moore") == normalize_name("DJ Moore") == "dj moore"
    assert normalize_name("Marvin Harrison Jr.") == "marvin harrison"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"
    assert normalize_position("RB12") == "RB"
    assert normalize_position("DST") == "DEF" and normalize_position("D/ST") == "DEF"
    assert normalize_team("JAC") == "JAX" and normalize_team("wsh") == "WAS"


def test_load_players_canonical_csv():
    csv_text = (
        "# comment line\n"
        "Player,Position,Team,ProjectedPoints,YahooADP,ADPStdDev,YahooPlayerID,RankAvg,RankStdDev\n"
        "Bijan Robinson,RB,ATL,315.7,1.2,3,12345,1.5,0.8\n"
        "D.J. Moore,WR,CHI,210,40.5,,,44,9.1\n"
        "Josh Allen,QB,BUF,390,,,,,\n"
        "Josh Allen,WR,JAX,50,,,,,\n"
        "Josh Allen,WR,LV,40,,,,,\n"
    )
    players = load_players(io.StringIO(csv_text))
    assert len(players) == 5
    b = players[0]
    assert (b.player_id, b.adp, b.adp_stddev, b.yahoo_player_id, b.rank_avg) == ("bijan robinson|RB", 1.2, 3.0, "12345", 1.5)
    assert players[1].player_id == "dj moore|WR"
    assert players[2].adp is None and players[2].yahoo_player_id is None
    # same name + position twice -> disambiguated by team
    ids = {p.player_id for p in players if p.name == "Josh Allen" and p.position == "WR"}
    assert ids == {"josh allen|WR|JAX", "josh allen|WR|LV"}


def test_load_players_from_raw_stats():
    csv_text = "Player,Pos,Team,PassYds,PassTD,Int,RushYds,RushTD,Rec,RecYds,RecTD,FumLost\n" \
               "QB Guy,QB,BUF,4000,30,10,500,5,0,0,0,2\n"
    scoring = {"4": 0.04, "5": 4, "6": -1, "9": 0.1, "10": 6, "11": 0.5, "12": 0.1, "13": 6, "18": -2}
    p = load_players(io.StringIO(csv_text), scoring=scoring)[0]
    assert p.projected_points == pytest.approx(160 + 120 - 10 + 50 + 30 - 4)
    assert points_from_stats({"Nothing": 1}, scoring) is None


def test_detect_format():
    assert detect_format(["RK", "TIERS", "PLAYER NAME", "TEAM", "POS"]) == FORMAT_FP_ECR
    assert detect_format(["Player", "Team", "ATT", "YDS", "FPTS"]) == FORMAT_FP_PROJECTIONS
    assert detect_format(["Name", "Pos", "Pts"]) == FORMAT_GENERIC


def test_read_fantasypros_ecr():
    rows = read_external(DATA / "fantasypros_ecr.csv")
    assert len(rows) == 9
    r = rows[4]
    assert r["name"] == "D.J. Moore" and r["position"] == "WR" and r["team"] == "CHI"
    assert (r["rank"], r["rank_avg"], r["rank_best"], r["rank_worst"], r["rank_stddev"], r["tier"]) == (5, 9.9, 3, 28, 6.8, 2)
    assert r["rank_scope"] == "overall"
    assert rows[8]["team"] == ""    # FA


def test_read_fantasypros_positional_ecr_marks_positional_scope():
    text = '"RK","TIERS","PLAYER NAME","TEAM","POS","BEST","WORST","AVG.","STD.DEV"\n' \
           '1,1,"Brock Bowers","LV","TE1",1,2,1.2,0.4\n2,1,"Trey McBride","ARI","TE2",1,4,2.1,0.9\n'
    rows = read_external(io.StringIO(text))
    assert all(r["rank_scope"] == "position" for r in rows)


def test_read_fantasypros_projections():
    rows = read_external(DATA / "fantasypros_projections_rb.csv", position="RB")
    assert len(rows) == 4
    assert rows[0]["name"] == "Bijan Robinson" and rows[0]["points"] == 315.2 and rows[0]["position"] == "RB"


def test_read_generic_with_column_map():
    text = "Nm,Position,Tm,Value,AvgRank,Sd\nJosh Allen,QB,BUF,88.5,12,4.4\n"
    rows = read_external(io.StringIO(text), fmt=FORMAT_GENERIC,
                         column_map={"name": "Nm", "vbd": "Value", "rank_avg": "AvgRank", "rank_stddev": "Sd", "team": "Tm"})
    assert rows[0]["vbd"] == 88.5 and rows[0]["rank_avg"] == 12 and rows[0]["rank_stddev"] == 4.4
    assert rows[0]["position"] == "QB" and rows[0]["team"] == "BUF"


def test_merge_external_matches_normalised_names_without_fuzzing():
    players = load_players(DATA / "players.csv")
    rows = read_external(DATA / "fantasypros_ecr.csv")
    result = merge_external(players, rows)
    assert result["matched"] == 8
    assert [r["name"] for r in result["unmatched"]] == ["Unknown Rookie"]
    dj = next(p for p in players if p.name == "DJ Moore")
    assert (dj.rank_avg, dj.rank_best, dj.rank_worst, dj.rank_stddev, dj.external_tier) == (9.9, 3, 28, 6.8, 2)
    mhj = next(p for p in players if p.name == "Marvin Harrison Jr.")
    assert mhj.rank_stddev == 7.5


def test_merge_external_projection_points_and_team_preference():
    a = Player(player_id="josh allen|WR|JAX", name="Josh Allen", team="JAX", position="WR", projected_points=0)
    b = Player(player_id="josh allen|WR|LV", name="Josh Allen", team="LV", position="WR", projected_points=0)
    rows = [{"name": "Josh Allen", "team": "LV", "position": "WR", "points": 77.0, "vbd": None, "adp": None,
             "rank": None, "rank_avg": None, "rank_best": None, "rank_worst": None, "rank_stddev": None, "tier": None}]
    merge_external([a, b], rows)
    assert b.projected_points == 77.0 and a.projected_points == 0


def test_external_vbd_blend_end_to_end():
    players = load_players(DATA / "players.csv")
    merge_external(players, read_external(DATA / "fantasypros_ecr.csv"))
    prepare_players(players, LeagueSettings(), {"external_vbd_weight": 0.5})
    chase = next(p for p in players if p.name == "Ja'Marr Chase")
    assert chase.external_vbd is not None and chase.external_vbd_scaled is not None
    assert chase.value != chase.vor
    assert chase.risk_label in ("SAFE", "BALANCED", "BOOM-BUST")


def test_mappings_round_trip(tmp_path):
    path = tmp_path / "player_mappings.csv"
    assert load_mappings(path) == {}
    save_mappings(path, {"dj moore|WR": "31234", "x|QB": "9"}, names={"dj moore|WR": "DJ Moore"})
    m = load_mappings(path)
    assert m == {"dj moore|WR": "31234", "x|QB": "9"}
    p = Player(player_id="dj moore|WR", name="DJ Moore", team="CHI", position="WR", projected_points=1)
    apply_mappings([p], m)
    assert p.yahoo_player_id == "31234"


def test_resolve_defense_spellings():
    from models import Player, resolve_player
    players = [Player("texans|DEF", "Texans", "HOU", "DEF", 100.0),
               Player("broncos|DEF", "Broncos", "DEN", "DEF", 90.0),
               Player("nico collins|WR", "Nico Collins", "HOU", "WR", 200.0)]
    for spelling, team in [("Texans", None), ("Houston Texans", None), ("Texans D/ST", None),
                           ("HOU DST", None), ("Houston", "HOU"), ("Texans Defense", "hou")]:
        assert resolve_player(players, spelling, "DST", team).name == "Texans", spelling
    assert resolve_player(players, "Broncos D/ST", "DEF").name == "Broncos"
    assert resolve_player(players, "Ravens D/ST", "DEF") is None
    assert resolve_player(players, "Nico Collins", "WR").team == "HOU"
