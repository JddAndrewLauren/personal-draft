import json

import pytest

from models import (
    DraftPick,
    DraftState,
    LeagueSettings,
    RosterConfig,
    default_teams,
    snake_slot_for_pick,
)


def make_state(user_slot=7, teams=12, rounds=15):
    settings = LeagueSettings(num_teams=teams, rounds=rounds)
    return DraftState(settings=settings, teams=default_teams(teams, user_slot), user_slot=user_slot)


def test_snake_order():
    assert snake_slot_for_pick(1, 12) == 1
    assert snake_slot_for_pick(12, 12) == 12
    assert snake_slot_for_pick(13, 12) == 12
    assert snake_slot_for_pick(24, 12) == 1
    assert snake_slot_for_pick(25, 12) == 1
    assert snake_slot_for_pick(30, 12) == 6


def test_user_picks_and_next_pick():
    st = make_state(user_slot=7)
    picks = st.user_picks()
    assert picks[:4] == [7, 18, 31, 42]
    assert len(picks) == 15
    assert st.current_pick == 1
    assert st.next_user_pick() == 7
    assert st.following_user_pick() == 18
    assert st.picks_until_user == 6
    assert not st.on_the_clock
    for i in range(1, 7):
        st.add_pick(f"p{i}")
    assert st.current_pick == 7
    assert st.on_the_clock
    assert st.next_user_pick() == 7
    assert st.following_user_pick() == 18
    assert st.picks_until_user == 0
    assert st.user_picks_remaining() == 15


def test_add_pick_defaults_and_validation():
    st = make_state()
    dp = st.add_pick("a", player_name="A")
    assert (dp.pick, dp.round, dp.slot, dp.source, dp.confirmed) == (1, 1, 1, "manual", False)
    with pytest.raises(ValueError):
        st.add_pick("a")            # already drafted
    with pytest.raises(ValueError):
        st.add_pick("b", pick=1)    # pick taken
    with pytest.raises(ValueError):
        st.add_pick("b", pick=999)  # outside draft
    dp2 = st.add_pick("b", slot=5)
    assert dp2.pick == 2 and dp2.slot == 5
    assert st.roster_for(5) == ["b"]


def test_undo_last():
    st = make_state()
    st.add_pick("a")
    st.add_pick("b")
    last = st.undo_last()
    assert last.player_id == "b"
    assert st.current_pick == 2
    assert st.drafted_ids() == {"a"}
    st.undo_last()
    assert st.undo_last() is None
    assert st.current_pick == 1


def test_current_round_and_completion():
    st = make_state(teams=2, rounds=2)
    assert st.current_round == 1
    st.add_pick("a"); st.add_pick("b"); st.add_pick("c")
    assert st.current_round == 2
    st.add_pick("d")
    assert st.is_complete
    assert st.next_user_pick() is None
    assert not st.on_the_clock


def yahoo_pick(n, pid, slot=None):
    return DraftPick(pick=n, round=(n - 1) // 12 + 1, slot=slot or snake_slot_for_pick(n, 12),
                     player_id=pid, source="yahoo", player_name=pid.upper(), team_key=f"t{slot}")


def test_merge_appends_and_confirms():
    st = make_state()
    st.add_pick("a", player_name="A")          # local manual pick 1
    new, conflicts = st.merge_yahoo([yahoo_pick(1, "a"), yahoo_pick(2, "b"), yahoo_pick(3, "c")])
    assert [p.pick for p in new] == [2, 3]
    assert conflicts == []
    assert st.pick_by_number(1).confirmed is True
    assert st.pick_by_number(1).source == "manual"
    assert st.pick_by_number(2).source == "yahoo" and st.pick_by_number(2).confirmed
    assert st.current_pick == 4
    # idempotent
    new, conflicts = st.merge_yahoo([yahoo_pick(1, "a"), yahoo_pick(2, "b"), yahoo_pick(3, "c")])
    assert new == [] and conflicts == []


def test_merge_conflict_is_surfaced_not_overwritten():
    st = make_state()
    st.add_pick("a", player_name="A")
    st.add_pick("x", player_name="X")          # local says pick 2 = x
    new, conflicts = st.merge_yahoo([yahoo_pick(1, "a"), yahoo_pick(2, "b")])
    assert new == []
    assert len(conflicts) == 1 and conflicts[0].pick == 2
    assert st.pick_by_number(2).player_id == "x"          # untouched
    # repeated sync does not duplicate the conflict
    _, again = st.merge_yahoo([yahoo_pick(1, "a"), yahoo_pick(2, "b")])
    assert again == [] and len(st.conflicts) == 1
    st.resolve_conflict(2, keep="yahoo")
    assert st.pick_by_number(2).player_id == "b"
    assert st.pick_by_number(2).confirmed and st.pick_by_number(2).source == "yahoo"
    assert st.conflicts == []


def test_merge_conflict_keep_local():
    st = make_state()
    st.add_pick("x", player_name="X")
    _, conflicts = st.merge_yahoo([yahoo_pick(1, "b")])
    assert len(conflicts) == 1
    st.resolve_conflict(1, keep="local")
    assert st.pick_by_number(1).player_id == "x" and st.pick_by_number(1).confirmed
    assert st.conflicts == []


def test_merge_same_player_different_pick_number_flags_conflict():
    st = make_state()
    st.add_pick("a")              # local: a at pick 1
    _, conflicts = st.merge_yahoo([yahoo_pick(2, "a")])
    assert len(conflicts) == 1 and conflicts[0].pick == 1
    assert st.current_pick == 2   # nothing appended


def test_undo_clears_conflict_on_that_pick():
    st = make_state()
    st.add_pick("x")
    st.merge_yahoo([yahoo_pick(1, "b")])
    assert st.conflicts
    st.undo_last()
    assert st.conflicts == []


def test_json_round_trip(tmp_path):
    st = make_state(user_slot=3)
    st.settings.roster = RosterConfig(slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "BN": 6})
    st.settings.scoring = {"4": 0.04, "11": 0.5}
    st.settings.league_key = "461.l.1"
    st.add_pick("a", player_name="A", team_key="461.l.1.t.1")
    st.add_pick("b")
    st.merge_yahoo([yahoo_pick(2, "z")])
    st.last_sync = 123.0
    st.sync_status = "connected"
    path = tmp_path / "draft_state.json"
    st.save(path)
    loaded = DraftState.load(path)
    assert loaded.user_slot == 3
    assert loaded.settings.roster.slots["WR"] == 2
    assert loaded.settings.scoring["11"] == 0.5
    assert loaded.settings.league_key == "461.l.1"
    assert [p.player_id for p in loaded.picks] == ["a", "b"]
    assert loaded.picks[0].team_key == "461.l.1.t.1"
    assert len(loaded.conflicts) == 1 and loaded.conflicts[0].pick == 2
    assert loaded.last_sync == 123.0 and loaded.sync_status == "connected"
    assert loaded.teams[2].is_user
    # file is valid JSON with a timestamp
    assert "saved_at" in json.loads(path.read_text())
