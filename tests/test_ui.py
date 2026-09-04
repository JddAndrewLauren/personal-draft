"""Tests for the pure presentation helpers in ui.py (no Streamlit runtime needed)."""
from models import Player, Recommendation
import ui


def mk_player(name, pos, pid=None, team="KC", proj=100.0, adp=10.0, risk="SAFE"):
    return Player(player_id=pid or name.lower().replace(" ", "-"), name=name, team=team, position=pos,
                  projected_points=proj, adp=adp, risk_label=risk)


def mk_rec(player, avail=0.5, survival=0.3):
    return Recommendation(player=player, score=1, adjusted_score=1, value=1, vor=1, survival=survival,
                          availability=avail, wait_cost=1, expected_alternative_value=0, alternative_name=None,
                          alternative_probability=0, roster_need=1)


def test_fragments_escape_player_text():
    evil = "<script>alert(1)</script>"
    row = ui.roster_grid([ui.RosterRow("RB", evil, 10.0)])
    hero = ui.rec_hero_html(1, evil, "RB", evil, "TAKE NOW", "SAFE", [("Score", "1", evil)], evil, [evil])
    for html in (row, hero, ui.badge(evil), ui.pos_chip(evil), ui.pill(evil), ui.wordmark(evil)):
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


def test_roster_rows_fill_overflow_and_unknown():
    slots = {"QB": 1, "RB": 2}
    rb1, rb2, rb3 = (mk_player(f"RB {i}", "RB") for i in range(3))
    rows = ui.roster_rows(slots, {"RB": [rb1, rb2, rb3]}, ["Mystery Man"])
    assert [(r.slot, r.name) for r in rows] == [
        ("QB", None), ("RB", "RB 0"), ("RB", "RB 1"), ("RB+", "RB 2"), ("?", "Mystery Man"),
    ]
    html = ui.roster_grid(rows)
    assert html.count("do-chip ghost") == 1          # the empty QB slot
    assert "Mystery Man" in html


def test_available_rows_filters():
    recs = [
        mk_rec(mk_player("Alpha Back", "RB", risk="SAFE")),
        mk_rec(mk_player("Beta Wide", "WR", risk="BOOM-BUST")),
        mk_rec(mk_player("Gamma Quarter", "QB", risk="SAFE")),
        mk_rec(mk_player("Delta Kicker", "K", risk="SAFE")),
    ]
    flex = ("RB", "WR", "TE")
    names = lambda rows: [r["Player"] for r in rows]  # noqa: E731
    assert names(ui.available_rows(recs, flex, False, 30, "ALL", "ALL", "")) == [
        "Alpha Back", "Beta Wide", "Gamma Quarter", "Delta Kicker"]
    assert names(ui.available_rows(recs, flex, False, 30, "FLEX", "ALL", "")) == ["Alpha Back", "Beta Wide"]
    assert names(ui.available_rows(recs, flex, False, 30, "QB", "ALL", "")) == ["Gamma Quarter"]
    assert names(ui.available_rows(recs, flex, False, 30, "ALL", "BOOM-BUST", "")) == ["Beta Wide"]
    assert names(ui.available_rows(recs, flex, False, 30, "ALL", "ALL", "  delta ")) == ["Delta Kicker"]
    # rank reflects position in the full list, not the filtered one
    assert ui.available_rows(recs, flex, False, 30, "K", "ALL", "")[0]["Rank"] == 4


def test_available_rows_on_the_clock_and_no_following_pick():
    rec = mk_rec(mk_player("Alpha Back", "RB"), avail=0.4, survival=0.2)
    off = ui.available_rows([rec], ("RB",), False, 30, "ALL", "ALL", "")[0]
    assert off["Reach my pick"] == 0.4 and off["Survival"] == 0.2
    on = ui.available_rows([rec], ("RB",), True, None, "ALL", "ALL", "")[0]
    assert on["Reach my pick"] is None and on["Survival"] is None


def test_table_columns():
    assert "Reach my pick" in ui.table_columns(False, False)
    assert "Reach my pick" not in ui.table_columns(True, False)
    assert "VOR" not in ui.table_columns(False, False)
    assert set(ui.EXTRA_COLUMNS) <= set(ui.table_columns(False, True))


def test_check_cards_marks_ok():
    html = ui.check_cards([("A", True, "fine"), ("B", False, "missing")])
    assert html.count("do-check ok") == 1 and html.count('class="do-check"') == 1
