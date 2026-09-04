"""enrich_players_csv: column merge into the master CSV keyed by name+position (DEF by team)."""
from models import enrich_key, enrich_players_csv, load_players


def test_enrich_key_defense_by_team_and_name_normalisation():
    assert enrich_key("Houston Texans", "DST", "HOU") == enrich_key("Texans", "DEF", "Hou")
    assert enrich_key("D.J. Moore", "WR") == enrich_key("DJ Moore Jr.", "WR", "BUF")


def test_enrich_players_csv_adds_columns_and_keeps_comment(tmp_path):
    p = tmp_path / "players.csv"
    p.write_text("# note\nPlayer,Position,Team,ProjectedPoints,YahooADP\n"
                 "Josh Allen,QB,BUF,331.9,20.6\nJosh Allen,WR,JAX,10,\nTexans,DEF,HOU,156.6,93.9\n")
    res = enrich_players_csv(p, {
        enrich_key("Josh Allen", "QB"): {"RankAvg": 25.07, "RankStdDev": 0.48, "_team": "BUF"},
        enrich_key("Houston Texans", "DST", "HOU"): {"RankAvg": 152.6, "RankStdDev": 5.0},
        enrich_key("Nobody", "RB"): {"RankAvg": 1},
    }, ["RankAvg", "RankStdDev"])
    assert res["matched"] == 2 and res["unmatched_keys"] == [enrich_key("Nobody", "RB")]
    text = p.read_text()
    assert text.startswith("# note\n")
    players = {(x.name, x.position): x for x in load_players(p)}
    assert players[("Josh Allen", "QB")].rank_avg == 25.07 and players[("Josh Allen", "QB")].rank_stddev == 0.48
    assert players[("Josh Allen", "WR")].rank_avg is None
    assert players[("Texans", "DEF")].rank_avg == 152.6
    assert players[("Josh Allen", "QB")].adp == 20.6          # untouched columns survive
