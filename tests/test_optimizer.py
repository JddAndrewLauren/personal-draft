import itertools
import math
import random

import pytest

from models import DraftState, LeagueSettings, Player, RosterConfig, default_teams
from optimizer import (
    DEFAULT_CONFIG,
    RecommendationContext,
    _action,
    assign_tiers,
    assign_roster_slots,
    blend_value,
    compute_vor,
    expected_best,
    fill_external_vbd_from_ranks,
    merge_config,
    prepare_players,
    recommend,
    Recommendation,
    replacement_ranks,
    risk_labels,
    roster_need,
    survival_probability,
    wait_cost,
)


def P(name, pos, pts, adp=None, **kw):
    return Player(player_id=f"{name}|{pos}", name=name, team="T", position=pos, projected_points=pts, adp=adp, **kw)


def pool(n_qb=20, n_rb=60, n_wr=70, n_te=20, seed=1):
    """Synthetic pool with monotone positional curves and ADP roughly by value."""
    rnd = random.Random(seed)
    players = []
    specs = {"QB": (n_qb, 380, 200), "RB": (n_rb, 300, 60), "WR": (n_wr, 290, 60), "TE": (n_te, 200, 60)}
    for pos, (n, top, floor) in specs.items():
        for i in range(n):
            pts = floor + (top - floor) * (1 - i / (n - 1)) ** 1.8
            players.append(P(f"{pos}{i+1}", pos, round(pts, 1)))
    settings = LeagueSettings(num_teams=12, rounds=15, roster=RosterConfig(
        slots={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "BN": 6}))
    compute_vor(players, settings)
    ordered = sorted(players, key=lambda p: p.vor + {"QB": -25, "TE": -5}.get(p.position, 0), reverse=True)
    for i, p in enumerate(ordered):
        p.adp = round(i + 1 + rnd.gauss(0, 1.5), 1)
    return players, settings


# ---------------------------------------------------------------- replacement / VOR / tiers

def test_replacement_ranks_from_roster_config():
    s = LeagueSettings(num_teams=12, roster=RosterConfig(slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "BN": 6}))
    assert replacement_ranks(s) == {"QB": 13, "RB": 31, "WR": 31, "TE": 13}
    s10 = LeagueSettings(num_teams=10, roster=RosterConfig(slots={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1, "BN": 5}))
    r = replacement_ranks(s10, {"RB": 0.5, "WR": 0.5, "TE": 0})
    assert r == {"QB": 11, "RB": 31, "WR": 41, "TE": 11, "K": 11, "DEF": 11}


def test_vor_uses_full_pool_replacement():
    players, settings = pool()
    rb = sorted([p for p in players if p.position == "RB"], key=lambda p: -p.projected_points)
    assert rb[30].vor == pytest.approx(0.0, abs=0.01)   # RB31 is replacement
    assert rb[0].vor == pytest.approx(rb[0].projected_points - rb[30].projected_points, abs=0.01)
    assert rb[-1].vor < 0


def test_value_scale_discounts_kickers():
    ps = [P(f"K{i}", "K", 150 - i * 3) for i in range(15)]
    s = LeagueSettings(num_teams=12, roster=RosterConfig(slots={"K": 1, "BN": 1}))
    compute_vor(ps, s, value_scale={"K": 0.5})
    assert ps[0].vor == pytest.approx((150 - (150 - 12 * 3)) * 0.5)


def test_tiers_break_on_gap_and_width():
    ps = [P("a", "TE", 200), P("b", "TE", 196), P("c", "TE", 170), P("d", "TE", 165), P("e", "TE", 160),
          P("f", "TE", 155), P("g", "TE", 150), P("h", "TE", 145)]
    assign_tiers(ps, {"TE": 10}, tier_widths={"TE": 20})
    assert [p.tier for p in ps] == [1, 1, 2, 2, 2, 2, 3, 3]


# ---------------------------------------------------------------- survival

def test_survival_is_conditional_and_monotone():
    # ADP 30 player still on the board at pick 45: survival to 50 is not ~0.
    s = survival_probability(30, 8, 45, 50)
    assert 0.2 < s < 0.9
    # conditioning matters: unconditional tail would be tiny
    assert s > survival_probability(30, 8, 1, 50) * 5
    # monotone in target pick
    vals = [survival_probability(40, 8, 30, t) for t in (31, 35, 40, 45, 55)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    assert survival_probability(40, 8, 30, 30) == 1.0


def test_survival_extremes():
    assert survival_probability(150, 10, 10, 20) > 0.99     # ADP far away: safe
    assert survival_probability(5, 4, 10, 30) < 0.05        # ADP long gone: won't last 20 more picks
    # far past ADP with underflowing tails still returns a sane number
    v = survival_probability(3, 2, 150, 160)
    assert 0.0 <= v <= 1.0


def test_expected_best_matches_brute_force():
    rnd = random.Random(3)
    cands = [(P(f"x{i}", "RB", 0), rnd.uniform(0, 60), rnd.uniform(0.05, 0.95)) for i in range(6)]
    exp, best_p, best_prob = expected_best(cands)
    brute = 0.0
    for outcome in itertools.product([0, 1], repeat=len(cands)):
        prob = 1.0
        for (_, _, s), o in zip(cands, outcome):
            prob *= s if o else (1 - s)
        vals = [v for (_, v, _), o in zip(cands, outcome) if o]
        brute += prob * (max(vals) if vals else 0.0)
    assert exp == pytest.approx(brute, rel=1e-9)
    assert best_p is not None and 0 < best_prob <= 1


def test_wait_cost_formula():
    assert wait_cost(58, 0.12, 34) == pytest.approx(0.88 * 24)
    assert wait_cost(30, 0.12, 40) == 0.0        # better alternatives expected: no cost
    assert wait_cost(58, 1.0, 0) == 0.0


# ---------------------------------------------------------------- roster need

ROSTER = RosterConfig(slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "BN": 6})


def test_roster_need_table():
    m = DEFAULT_CONFIG["need_multipliers"]
    assert roster_need("RB", {}, ROSTER, 15).multiplier == m["starter_open"]
    assert roster_need("RB", {"RB": 2}, ROSTER, 12).multiplier == m["flex_open"]
    assert roster_need("RB", {"RB": 3}, ROSTER, 12).multiplier == m["bench"]           # flex used
    assert roster_need("RB", {"RB": 4}, ROSTER, 12).multiplier == m["bench_deep"]
    assert roster_need("QB", {"QB": 1}, ROSTER, 12).multiplier == m["bench"]
    assert roster_need("QB", {"QB": 2}, ROSTER, 12).multiplier == 0.0                 # max_per_position
    assert roster_need("K", {}, ROSTER, 12).multiplier == 0.0                         # not rosterable here


def test_roster_need_hard_late_draft_rule():
    counts = {"QB": 1, "RB": 4, "WR": 4, "TE": 0}
    # 1 open starter (TE), 1 pick left -> only TE
    assert roster_need("TE", counts, ROSTER, 1).multiplier > 0
    assert roster_need("WR", counts, ROSTER, 1).multiplier == 0.0
    assert roster_need("WR", counts, ROSTER, 2).multiplier > 0
    full = {"QB": 1, "RB": 4, "WR": 4, "TE": 2, "FLEX": 0}
    assert roster_need("RB", {"QB": 1, "RB": 5, "WR": 5, "TE": 2}, ROSTER, 1).multiplier == 0.0  # roster full


def test_assign_roster_slots():
    ps = [P("rb1", "RB", 250), P("rb2", "RB", 200), P("rb3", "RB", 180), P("wr1", "WR", 220), P("qb", "QB", 300),
          P("wr2", "WR", 150), P("wr3", "WR", 140)]
    slots = assign_roster_slots(ps, ROSTER)
    assert [p.name for p in slots["RB"]] == ["rb1", "rb2"]
    assert [p.name for p in slots["WR"]] == ["wr1", "wr2"]
    assert [p.name for p in slots["FLEX"]] == ["rb3"]
    assert [p.name for p in slots["BN"]] == ["wr3"]


# ---------------------------------------------------------------- VBD blend and risk

def test_blend_value_rescales_external_vbd():
    ps = [P(f"p{i}", "RB", 100 + i) for i in range(10)]
    for i, p in enumerate(ps):
        p.vor = 10.0 * i
        p.external_vbd = 5.0 * i + 100        # perfectly linear, different units
    blend_value(ps, 0.5)
    for i, p in enumerate(ps):
        assert p.external_vbd_scaled == pytest.approx(10.0 * i, abs=0.01)
        assert p.value == pytest.approx(10.0 * i, abs=0.01)
    ps[3].external_vbd = None
    blend_value(ps, 1.0)
    assert ps[3].value == ps[3].vor


def test_fill_external_vbd_from_ranks_overall_and_positional():
    ps = [P("a", "RB", 300), P("b", "WR", 280), P("c", "RB", 200), P("d", "WR", 150)]
    for p, v in zip(ps, (100, 80, 40, 10)):
        p.vor = v
    ps[2].rank_avg, ps[2].rank_scope = 2, "overall"      # 2nd best overall -> 80
    ps[3].rank_avg, ps[3].rank_scope = 1, "position"     # best WR -> 80
    assert fill_external_vbd_from_ranks(ps) == 2
    assert ps[2].external_vbd == 80 and ps[3].external_vbd == 80
    assert ps[0].external_vbd is None


def test_risk_labels_relative_to_position():
    ps = []
    for i in range(12):
        p = P(f"w{i}", "WR", 200 - i)
        p.rank_avg = 10 + i
        p.rank_stddev = 3.0
        p.rank_best, p.rank_worst = p.rank_avg - 3, p.rank_avg + 3
        ps.append(p)
    ps[0].rank_stddev, ps[0].rank_best, ps[0].rank_worst = 12.0, 1, 40    # wild disagreement
    ps[1].rank_stddev, ps[1].rank_best, ps[1].rank_worst = 0.5, 11, 12    # consensus
    nodata = P("q", "QB", 300)
    risk_labels(ps + [nodata])
    assert ps[0].risk_label == "BOOM-BUST"
    assert ps[1].risk_label == "SAFE"
    assert nodata.risk_label == "—" and nodata.risk_score is None


# ---------------------------------------------------------------- recommend: sanity scenarios (spec §35)

def make_state(settings, user_slot=7):
    return DraftState(settings=settings, teams=default_teams(settings.num_teams, user_slot), user_slot=user_slot)


def draft_by_adp(state, players, n, exclude=()):
    avail = sorted((p for p in players if p.player_id not in state.drafted_ids() and p.player_id not in exclude),
                   key=lambda p: p.adp)
    for p in avail[:n]:
        state.add_pick(p.player_id, player_name=p.name)


def test_recommend_runs_fast_and_returns_all_available():
    players, settings = pool()
    prepare_players(players, settings)
    st = make_state(settings)
    draft_by_adp(st, players, 6)
    import time
    t = time.perf_counter()
    recs = recommend(st, players)
    assert (time.perf_counter() - t) < 0.5
    assert len(recs) == len(players) - 6
    assert recs[0].adjusted_score >= recs[1].adjusted_score
    assert recs[0].reasons and recs[0].confidence in ("STRONG", "MODERATE", "CLOSE")


def test_elite_player_falling_rises_dramatically():
    players, settings = pool()
    prepare_players(players, settings)
    st = make_state(settings, user_slot=8)
    elite = next(p for p in players if p.name == "RB1")
    # Normal: elite RB gone by the user's pick at 8
    draft_by_adp(st, players, 7)
    base_top = recommend(st, players)[0]
    # Scenario: everyone passes on RB1 through pick 19 (user at 20 in slot 5 of round 2? use slot 8 -> pick 17)
    st2 = make_state(settings, user_slot=8)
    draft_by_adp(st2, players, 16, exclude={elite.player_id})
    recs = recommend(st2, players)
    assert recs[0].player is elite
    assert recs[0].survival < 0.2                       # he will not last another round
    assert recs[0].wait_cost > 10
    assert any("falling" in r.lower() for r in recs[0].reasons)


def test_qb_already_filled_reduces_qb_pressure():
    players, settings = pool()
    prepare_players(players, settings)
    st = make_state(settings, user_slot=1)
    draft_by_adp(st, players, 24)           # user is on the clock at pick 25 (slot 1, round 3)
    assert st.on_the_clock
    qb_rank_before = next(i for i, r in enumerate(recommend(st, players)) if r.player.position == "QB")
    # Now give the user an elite QB instead of their pick-1 player
    st2 = make_state(settings, user_slot=1)
    qb1 = next(p for p in players if p.name == "QB1")
    st2.add_pick(qb1.player_id, pick=1)
    draft_by_adp(st2, players, 23)
    recs2 = recommend(st2, players)
    qb_rank_after = next(i for i, r in enumerate(recs2) if r.player.position == "QB")
    qb_need = next(r for r in recs2 if r.player.position == "QB").roster_need
    assert qb_need < 1.0
    assert qb_rank_after > qb_rank_before


def test_tier_cliff_raises_wait_cost():
    players, settings = pool()
    # Make TE1 clearly elite and the rest of the TEs a flat cliff
    for p in players:
        if p.position == "TE":
            p.projected_points = 200 if p.name == "TE1" else 120 - int(p.name[2:])
    prepare_players(players, settings)
    for p in players:
        if p.position == "TE":
            p.adp = 22 if p.name == "TE1" else 120 + int(p.name[2:])
    st = make_state(settings, user_slot=7)
    draft_by_adp(st, players, 17)              # slot 7 is on the clock at pick 18, next at 31
    assert st.on_the_clock and st.current_pick == 18
    te1 = next(r for r in recommend(st, players) if r.player.name == "TE1")
    assert te1.survival < 0.3
    assert te1.wait_cost > 20
    assert te1.expected_alternative_value < te1.value - 20
    assert any("Last Tier 1 TE" in r for r in te1.reasons)


def test_deep_wr_tier_keeps_urgency_low():
    players, settings = pool()
    prepare_players(players, settings)
    # five near-identical WRs with ADP just after the user's following pick
    st = make_state(settings, user_slot=7)
    draft_by_adp(st, players, 6)
    wrs = [p for p in players if p.position == "WR" and p.player_id not in st.drafted_ids()]
    wrs = sorted(wrs, key=lambda p: -p.projected_points)[:5]
    for p in wrs:
        p.projected_points = 250.0
        p.value = p.vor = 90.0
        p.adp = 15.0
    recs = {r.player.player_id: r for r in recommend(st, players)}
    for p in wrs:
        assert recs[p.player_id].wait_cost < 8       # someone equivalent will be there


def test_high_survival_prefers_waiting():
    players, settings = pool()
    prepare_players(players, settings)
    st = make_state(settings, user_slot=7)
    draft_by_adp(st, players, 6)
    # Two RBs of equal value; one will surely survive, one surely won't.
    a, b = [p for p in players if p.position == "RB" and p.player_id not in st.drafted_ids()][:2]
    a.value = b.value = 100.0
    a.adp, b.adp = 8.0, 60.0
    ordered = recommend(st, players)
    recs = {r.player.player_id: r for r in ordered}
    assert recs[b.player_id].survival > 0.95
    assert recs[b.player_id].wait_cost < 1.0
    assert recs[a.player_id].survival < 0.15
    # Equal value: the analytical model sees no cost in waiting on either (the other is a
    # perfect fallback), so the tie is broken toward the player less likely to survive.
    assert recs[a.player_id].adjusted_score >= recs[b.player_id].adjusted_score
    assert ordered.index(recs[a.player_id]) < ordered.index(recs[b.player_id])


def test_last_pick_has_no_wait_cost():
    players, settings = pool()
    prepare_players(players, settings)
    settings.rounds = 13                       # pool has 170 players; 12 × 13 = 156 picks
    st = make_state(settings, user_slot=7)
    last_user = st.user_picks()[-1]
    draft_by_adp(st, players, last_user - 1)
    assert st.on_the_clock and st.following_user_pick() is None
    recs = recommend(st, players)
    assert all(r.wait_cost == 0 for r in recs)
    assert recs[0].action == "LAST PICK"


def _rec(survival, wait_cost):
    return Recommendation(player=P("X", "RB", 200.0), score=0.0, adjusted_score=0.0, value=100.0, vor=50.0,
                          survival=survival, availability=1.0, wait_cost=wait_cost,
                          expected_alternative_value=0.0, alternative_name=None,
                          alternative_probability=0.0, roster_need=1.0)


def _ctx(following=30):
    return RecommendationContext(current_pick=19, my_pick=19, following_pick=following,
                                 on_the_clock=True, picks_remaining=10)


def test_action_rank0_reflects_urgency():
    assert _action(_rec(0.87, 0.4), 0, "CLOSE", _ctx()) == "CLOSE DECISION"
    assert _action(_rec(0.87, 0.4), 0, "STRONG", _ctx()) == "SAFE TO WAIT"
    assert _action(_rec(0.15, 12.0), 0, "STRONG", _ctx()) == "TAKE NOW"
    # High survival but a real cost of waiting (fallback is much weaker) is still urgent.
    assert _action(_rec(0.85, 2.5), 0, "MODERATE", _ctx()) == "TAKE NOW"
    assert _action(_rec(0.87, 0.4), 0, "STRONG", _ctx(following=None)) == "LAST PICK"
    assert _action(_rec(0.87, 0.4), 1, "STRONG", _ctx()) == "LIKELY AVAILABLE LATER"



    players, settings = pool()
    prepare_players(players, settings)
    st = make_state(settings, user_slot=12)     # first pick at 12
    draft_by_adp(st, players, 3)
    recs = recommend(st, players)
    top = recs[0]
    assert top.availability > 0.5                # a player likely to reach pick 12
    best_value = max(recs, key=lambda r: r.value)
    assert best_value.availability < 0.5         # the true best player will be gone


def test_merge_config_nested():
    cfg = merge_config({"wait_cost_weight": 2.0, "need_multipliers": {"bench": 0.5}})
    assert cfg["wait_cost_weight"] == 2.0
    assert cfg["need_multipliers"]["bench"] == 0.5
    assert cfg["need_multipliers"]["starter_open"] == DEFAULT_CONFIG["need_multipliers"]["starter_open"]
    assert DEFAULT_CONFIG["need_multipliers"]["bench"] == 0.85    # defaults untouched
