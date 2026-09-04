import json
import time
from pathlib import Path

import pytest

from models import DraftState, Player, Team, default_teams, load_players
from yahoo import (
    YahooAuthError,
    YahooClient,
    assign_draft_slots,
    build_mappings,
    draft_picks_from_results,
    fill_adp_from_yahoo,
    load_yahoo_players,
    parse_draft_results,
    parse_leagues,
    parse_players,
    parse_settings,
    parse_teams,
    parse_xml,
    save_yahoo_players,
)

FIX = Path(__file__).resolve().parent.parent / "test-data" / "yahoo"


def root(name):
    return parse_xml((FIX / name).read_text())


def test_parse_leagues():
    lgs = parse_leagues(root("leagues.xml"))
    assert [l["league_key"] for l in lgs] == ["461.l.12345", "461.l.99999"]
    assert lgs[0]["name"] == "Twinion Invitational" and lgs[0]["num_teams"] == 12
    assert lgs[0]["draft_status"] == "predraft" and lgs[1]["draft_status"] == "postdraft"


def test_parse_settings_roster_flex_scoring():
    s = parse_settings(root("settings.xml"))
    assert s.league_key == "461.l.12345" and s.name == "Twinion Invitational"
    assert s.num_teams == 12
    assert s.roster.slots == {"QB": 1, "WR": 2, "RB": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1, "BN": 5}
    assert s.roster.flex_positions == ("RB", "WR", "TE")
    assert s.rounds == 15                      # IR slots are not draft rounds
    assert s.scoring["11"] == 1.0 and s.scoring["4"] == 0.04 and s.scoring["18"] == -2.0


def test_parse_teams_marks_user():
    teams = parse_teams(root("teams.xml"))
    assert [t.name for t in teams] == ["Gridiron Gang", "Twinion FC", "Bench Warmers", "Waiver Wire Heroes"]
    assert [t.is_user for t in teams] == [False, True, False, False]
    assert teams[1].team_key == "461.l.12345.t.2"


def test_parse_draft_results_skips_incomplete_picks():
    res = parse_draft_results(root("draftresults.xml"))
    assert [r["pick"] for r in res] == [1, 2, 3, 4, 5, 6]
    assert res[0]["yahoo_player_id"] == "40001" and res[0]["team_key"] == "461.l.12345.t.3"
    assert res[4]["round"] == 2


def test_parse_players_with_draft_analysis():
    rows = parse_players(root("players.xml"))
    assert len(rows) == 6
    bijan = rows[0]
    assert bijan == {"yahoo_player_id": "40001", "name": "Bijan Robinson", "team": "ATL", "position": "RB",
                     "adp": 1.4, "percent_drafted": 1.0, "bye": 5}
    assert rows[4]["position"] == "DEF" and rows[4]["team"] == "BAL"
    assert rows[5]["adp"] is None            # "-" average pick


def test_assign_draft_slots_from_round_one():
    teams = parse_teams(root("teams.xml"))
    res = parse_draft_results(root("draftresults.xml"))
    slots = assign_draft_slots(teams, res, num_teams=4)
    assert slots == {"461.l.12345.t.3": 1, "461.l.12345.t.1": 2, "461.l.12345.t.4": 3, "461.l.12345.t.2": 4}
    assert next(t for t in teams if t.is_user).slot == 4


def test_build_mappings_and_draft_picks():
    players = load_players(FIX.parent / "placeholder_players.csv")
    yrows = parse_players(root("players.xml"))
    result = build_mappings(players, yrows, manual={"josh allen|QB": "40006"})
    m = result["mapping"]
    assert m["bijan robinson|RB"] == "40001"
    assert m["jamarr chase|WR"] == "40002"
    assert m["dj moore|WR"] == "40003"                 # D.J. vs DJ
    assert m["marvin harrison|WR"] == "40004"          # Jr. suffix
    assert m["ravens d st|DEF"] == "40005"             # team defense matched by team abbreviation
    assert m["josh allen|QB"] == "40006"               # manual mapping wins
    assert result["matched"] == 6
    assert len(result["unmatched"]) == len(players) - 6

    # ADP fill from Yahoo draft analysis
    for p in players:
        p.yahoo_player_id = m.get(p.player_id)
        p.adp = None
    n = fill_adp_from_yahoo(players, yrows)
    assert n == 5                                       # Josh Allen has no ADP
    assert next(p for p in players if p.name == "DJ Moore").adp == 38.7

    # Yahoo draft results -> DraftPicks (unmapped player gets a placeholder id)
    teams = parse_teams(root("teams.xml"))
    res = parse_draft_results(root("draftresults.xml"))
    assign_draft_slots(teams, res, 4)
    yahoo_to_local = {v: k for k, v in m.items()}
    picks = draft_picks_from_results(res, teams, yahoo_to_local, 4, {r["yahoo_player_id"]: r["name"] for r in yrows})
    assert [p.player_id for p in picks][:5] == ["bijan robinson|RB", "jamarr chase|WR", "dj moore|WR",
                                                 "marvin harrison|WR", "ravens d st|DEF"]
    assert picks[5].player_id == "yahoo:49999"
    assert [p.slot for p in picks] == [1, 2, 3, 4, 4, 3]
    assert all(p.source == "yahoo" and p.confirmed for p in picks)

    # Merge into local state where the user had manually entered pick 1 already
    st = DraftState(teams=teams, user_slot=4)
    st.settings.num_teams = 4
    st.settings.rounds = 15
    st.add_pick("bijan robinson|RB", player_name="Bijan Robinson")
    new, conflicts = st.merge_yahoo(picks)
    assert [p.pick for p in new] == [2, 3, 4, 5, 6] and conflicts == []
    assert st.pick_by_number(1).confirmed


def test_yahoo_player_cache_round_trip(tmp_path):
    rows = parse_players(root("players.xml"))
    path = tmp_path / "yahoo_players.csv"
    save_yahoo_players(rows, path)
    back = load_yahoo_players(path)
    assert back == rows


class FakeResponse:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload


class FakeSession:
    """Scripted HTTP session: records calls, returns canned responses."""

    def __init__(self, get_responses, post_responses):
        self.gets, self.posts = list(get_responses), list(post_responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        return self.gets.pop(0)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        return self.posts.pop(0)


def test_client_requires_credentials(tmp_path):
    with pytest.raises(YahooAuthError):
        YahooClient("", "", token_path=tmp_path / "t.json")


def test_client_exchange_refresh_and_401_retry(tmp_path):
    tok_path = tmp_path / "t.json"
    xml = (FIX / "leagues.xml").read_text()
    session = FakeSession(
        get_responses=[FakeResponse(401, "expired"), FakeResponse(200, xml)],
        post_responses=[
            FakeResponse(200, payload={"access_token": "A1", "refresh_token": "R1", "expires_in": 3600}),
            FakeResponse(200, payload={"access_token": "A2", "expires_in": 3600}),
        ],
    )
    c = YahooClient("id", "secret", token_path=tok_path, session=session)
    assert not c.has_token
    assert "client_id=id" in c.authorize_url() and "redirect_uri=https%3A%2F%2Flocalhost%3A8501%2F" in c.authorize_url()
    c.exchange_code(" CODE123 ")
    assert c.has_token and json.loads(tok_path.read_text())["refresh_token"] == "R1"
    assert session.calls[0][2]["code"] == "CODE123"

    lgs = parse_leagues(c.get_xml("users;use_login=1/games;game_keys=nfl/leagues"))
    assert len(lgs) == 2
    # 401 -> refresh (keeps old refresh token) -> retry with new access token
    assert session.calls[2][2]["grant_type"] == "refresh_token"
    assert c.token["access_token"] == "A2" and c.token["refresh_token"] == "R1"
    assert session.calls[3][2]["Authorization"] == "Bearer A2"

    # a fresh client picks up the cached token and refreshes when expired
    c2 = YahooClient("id", "secret", token_path=tok_path, session=FakeSession(
        [], [FakeResponse(200, payload={"access_token": "A3", "expires_in": 3600})]))
    c2.token["expires_at"] = time.time() - 1
    assert c2.ensure_token() == "A3"
    c2.clear_token()
    assert not tok_path.exists()
