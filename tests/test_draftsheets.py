"""DraftSheets re-scoring: the workbook math under our settings, on a tiny synthetic workbook."""
from draftsheets import _points, _rookie_bonus, _tiers, compute, settings_from_league

BASE = {"teams": 2, "QB": 1, "RB": 1, "WR": 1, "TE": 1, "FLEX": 1, "BENCH": 1, "SUPERFLEX": 0,
        "PassINCMP": 0, "PassCMP": 0, "PassYDS": 25, "PassTDs": 4, "INTS": -1, "SACKS": 0,
        "RushATT": 0, "RushYDS": 10, "RushTDS": 6, "PPR_RB": 0.5, "PPR_WR": 0.5, "PPR_TE": 0.5,
        "RecYDS": 10, "RecTDS": 6, "FL": -2, "Pass1D": 0, "Rush1D": 0, "Rec1D": 0,
        "flex_positions": ("RB", "WR", "TE")}


def test_points_follow_scoring_inputs():
    # RB row: Player, Team, ATT, YDS, TDS, REC, YDS, TDS, FL
    rb = ("X", "T", 200, 1000, 10, 50, 400, 2, 1)
    half = _points("RB", rb, BASE)
    std = _points("RB", rb, {**BASE, "PPR_RB": 0})
    assert half == 100 + 60 + 25 + 40 + 12 - 2
    assert half - std == 25                       # 50 receptions x 0.5
    qb = ("Q", "T", 500, 320, 4000, 30, 10, 60, 300, 3, 2)
    assert _points("QB", qb, BASE) == 160 + 120 - 10 + 30 + 18 - 4


def test_rookie_bonus_only_below_threshold():
    assert _rookie_bonus("RB", 17 * 14.9, True) == 0
    assert round(_rookie_bonus("RB", 17 * 10, True), 3) == round(17 * 0.258 * 4.9, 3)
    assert _rookie_bonus("WR", 0, False) == 0 and _rookie_bonus("TE", 0, True) == 0


def test_tiers_break_on_relative_gap():
    t = _tiers([100, 90, 60, 58, 20], gap=0.2, cap=15)
    assert t[100] == 1 and t[90] == 1 and t[60] == 2 and t[58] == 2 and t[20] == 3


def _raw():
    def rows(pos, players):
        return {n: {"team": "T", "avg": r, "high": r, "low": r} for n, r in players.items()}
    stats = {
        "QB": rows("QB", {f"QB{i}": ("", "T", 500, 320, 4000 - 200 * i, 30, 10, 0, 0, 0, 0) for i in range(4)}),
        "RB": rows("RB", {f"RB{i}": ("", "T", 200, 1200 - 100 * i, 8, 40, 300, 1, 0) for i in range(6)}),
        "WR": rows("WR", {f"WR{i}": ("", "T", 80, 1100 - 100 * i, 7, 0, 0, 0, 0) for i in range(6)}),
        "TE": rows("TE", {f"TE{i}": ("", "T", 60, 700 - 100 * i, 5, 0) for i in range(4)}),
    }
    ecr, rank = [], 0
    for pos, n in (("RB", 6), ("WR", 6), ("QB", 4), ("TE", 4)):
        for i in range(n):
            rank += 1
            ecr.append({"rank": rank, "tier": 1, "name": f"{pos}{i}", "team": "T", "pos": pos, "pos_rank": i + 1,
                        "bye": 5, "upside": "", "bust": "", "sos": "", "ecr_vs_adp": 0})
    risk = {f"{p}{i}": 1.0 for p in ("QB", "RB", "WR", "TE") for i in range(1, 7)}
    return {"stats": stats, "ecr": ecr, "risk": risk, "rookies": {"RB5"}}


def test_compute_vbd_against_baseline_and_flex_split():
    out, meta = compute(_raw(), {**BASE, "PPR_RB": 0, "PPR_WR": 0, "PPR_TE": 0, "flex_positions": ("RB", "WR")})
    assert meta["flex_share"]["TE"] == 0                      # TE excluded from the flex race
    assert meta["flex_n"] == round(1 * 2 * 1.4)
    by = {r["name"]: r for r in out}
    # Best RB has the largest VBD at the position; VBD == zscore - baseline points for that position.
    assert by["RB0"]["vbd"] > by["RB1"]["vbd"] > by["RB5"]["vbd"]
    assert abs(by["RB0"]["vbd"] - (by["RB0"]["zscore"] - meta["baseline_pts"]["RB"])) < 1e-9
    # Games-missed discount: points scaled by (16 - missed) / 17 when low == avg == high and slot == own avg.
    assert by["RB5"]["sheet_tier"] >= by["RB0"]["sheet_tier"]
    assert all(r["sheet_tier"] >= 1 for r in out)


def test_settings_from_league_maps_yahoo_stat_ids():
    cfg = {"league": {"teams": 12, "roster": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DEF": 1, "BN": 6},
                      "flex_positions": ["RB", "WR"],
                      "scoring": {"4": 0.04, "5": 4, "6": -1, "9": 0.1, "10": 6, "11": 0, "12": 0.1, "13": 6, "18": -2}},
           "draft": {"rounds": 15}}
    s = settings_from_league(cfg, BASE)
    assert (s["WR"], s["FLEX"], s["BENCH"], s["teams"]) == (3, 1, 6, 12)
    assert s["PPR_RB"] == 0 and s["PassYDS"] == 25 and s["RushYDS"] == 10 and s["FL"] == -2
    assert s["flex_positions"] == ("RB", "WR")
