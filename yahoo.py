"""Yahoo Fantasy Sports read-only integration: OAuth2, requests, XML -> internal models.

Nothing here knows about the optimizer.  All network access goes through ``YahooClient``;
the ``parse_*`` functions take an already-parsed XML root so they can be unit-tested against
recorded responses in test-data/yahoo/.
"""
from __future__ import annotations

import base64
import csv
import json
import logging
import time
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from models import (
    DraftPick,
    LeagueSettings,
    Player,
    RosterConfig,
    Team,
    normalize_name,
    normalize_position,
    normalize_team,
    snake_slot_for_pick,
)

log = logging.getLogger("fantasy-draft.yahoo")

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2/"
REDIRECT_URI = "https://localhost:8501/"  # must match the Yahoo app; Yahoo no longer accepts "oob"
PAGE_SIZE = 25

# Yahoo roster position names -> our position keys. FLEX-type slots map to "FLEX".
FLEX_SLOT_NAMES = {"W/R/T": ("RB", "WR", "TE"), "W/R": ("RB", "WR"), "W/T": ("WR", "TE"),
                   "R/T": ("RB", "TE"), "Q/W/R/T": ("QB", "RB", "WR", "TE")}
IGNORED_SLOTS = {"IR", "IR+", "IL", "IL+", "NA"}


class YahooError(RuntimeError):
    pass


class YahooAuthError(YahooError):
    pass


# --------------------------------------------------------------------------- #
# OAuth2 + HTTP
# --------------------------------------------------------------------------- #


def extract_code(text: str) -> str:
    """Accept either the bare authorization code or the full redirect URL Yahoo sent the browser to."""
    text = text.strip()
    if "code=" in text:
        qs = parse_qs(urlparse(text if "://" in text else "http://x/?" + text).query)
        if qs.get("code"):
            return qs["code"][0]
    return text


class YahooClient:
    """Minimal OAuth2 (authorization-code, out-of-band) client with a cached token file."""

    def __init__(self, client_id: str, client_secret: str, token_path="~/.yahoo_token.json",
                 session: Optional[requests.Session] = None, timeout: float = 10.0):
        if not client_id or not client_secret:
            raise YahooAuthError("YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET are not set (see .env.example)")
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_path = Path(token_path).expanduser()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.token: dict = {}
        if self.token_path.exists():
            try:
                self.token = json.loads(self.token_path.read_text())
            except json.JSONDecodeError:
                self.token = {}

    # ---- token lifecycle ---------------------------------------------------- #
    @property
    def has_token(self) -> bool:
        return bool(self.token.get("refresh_token"))

    def authorize_url(self) -> str:
        return AUTH_URL + "?" + urlencode({
            "client_id": self.client_id, "redirect_uri": REDIRECT_URI,
            "response_type": "code", "language": "en-us", "scope": "fspt-r",
        })

    def open_browser(self) -> None:
        webbrowser.open(self.authorize_url())

    def _basic_auth_header(self) -> dict:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def _token_request(self, data: dict) -> dict:
        r = self.session.post(TOKEN_URL, data=data, headers=self._basic_auth_header(), timeout=self.timeout)
        if r.status_code != 200:
            raise YahooAuthError(f"Token request failed ({r.status_code}): {r.text[:200]}")
        tok = r.json()
        tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
        if "refresh_token" not in tok and self.token.get("refresh_token"):
            tok["refresh_token"] = self.token["refresh_token"]
        self.token = tok
        self.token_path.write_text(json.dumps(tok))
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        return tok

    def exchange_code(self, code: str) -> dict:
        return self._token_request({"grant_type": "authorization_code", "redirect_uri": REDIRECT_URI,
                                    "code": extract_code(code)})

    def refresh(self) -> dict:
        if not self.has_token:
            raise YahooAuthError("No refresh token; authorize first")
        return self._token_request({"grant_type": "refresh_token", "redirect_uri": REDIRECT_URI,
                                    "refresh_token": self.token["refresh_token"]})

    def ensure_token(self) -> str:
        if not self.has_token:
            raise YahooAuthError("Not authorized with Yahoo yet")
        if not self.token.get("access_token") or time.time() >= float(self.token.get("expires_at", 0)):
            self.refresh()
        return self.token["access_token"]

    def clear_token(self) -> None:
        self.token = {}
        if self.token_path.exists():
            self.token_path.unlink()

    # ---- requests ------------------------------------------------------------ #
    def get_xml(self, path: str) -> ET.Element:
        """GET an API path (relative to API_BASE) and return the namespace-stripped XML root."""
        url = API_BASE + path.lstrip("/")
        for attempt in (1, 2):
            token = self.ensure_token()
            r = self.session.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
            if r.status_code == 401 and attempt == 1:
                log.info("Yahoo 401; refreshing token")
                self.refresh()
                continue
            if r.status_code != 200:
                raise YahooError(f"Yahoo {r.status_code} for {path}: {r.text[:300]}")
            return parse_xml(r.text)
        raise YahooError(f"Yahoo request failed after retry: {path}")


def parse_xml(text: str) -> ET.Element:
    root = ET.fromstring(text)
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: Optional[ET.Element], path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text is not None else default


# --------------------------------------------------------------------------- #
# Parsers (pure: XML root -> models / dicts)
# --------------------------------------------------------------------------- #


def parse_leagues(root: ET.Element) -> list:
    """users;use_login=1/games;game_keys=nfl/leagues -> [{league_key, name, num_teams, draft_status, season}]"""
    out = []
    for lg in root.iter("league"):
        out.append({
            "league_key": _text(lg, "league_key"),
            "name": _text(lg, "name"),
            "num_teams": int(_text(lg, "num_teams", "0") or 0),
            "draft_status": _text(lg, "draft_status"),
            "season": _text(lg, "season"),
            "draft_time": _text(lg, "draft_time"),
        })
    return out


def parse_settings(root: ET.Element) -> LeagueSettings:
    """league/{key}/settings -> LeagueSettings (roster slots, FLEX, scoring modifiers)."""
    lg = root.find(".//league")
    if lg is None:
        raise YahooError("No <league> in settings response")
    slots: dict = {}
    flex_positions: tuple = ("RB", "WR", "TE")
    for rp in lg.iter("roster_position"):
        pos = _text(rp, "position")
        count = int(_text(rp, "count", "0") or 0)
        if pos in IGNORED_SLOTS or count <= 0:
            continue
        if pos in FLEX_SLOT_NAMES:
            slots["FLEX"] = slots.get("FLEX", 0) + count
            flex_positions = FLEX_SLOT_NAMES[pos]
        else:
            key = normalize_position(pos) if pos not in ("BN",) else "BN"
            slots[key] = slots.get(key, 0) + count
    scoring = {}
    mods = lg.find(".//stat_modifiers")
    if mods is not None:
        for st in mods.iter("stat"):
            sid, val = _text(st, "stat_id"), _text(st, "value")
            try:
                scoring[sid] = float(val)
            except ValueError:
                pass
    roster = RosterConfig(slots=slots or RosterConfig().slots, flex_positions=tuple(flex_positions))
    num_teams = int(_text(lg, "num_teams", "12") or 12)
    return LeagueSettings(
        num_teams=num_teams,
        rounds=roster.total_slots,
        roster=roster,
        scoring=scoring,
        league_key=_text(lg, "league_key") or None,
        name=_text(lg, "name") or "Yahoo league",
    )


def parse_draft_status(root: ET.Element) -> str:
    return _text(root.find(".//league"), "draft_status")


def parse_teams(root: ET.Element) -> list:
    """league/{key}/teams -> [Team]; is_user from is_owned_by_current_login / manager.is_current_login."""
    out = []
    for i, t in enumerate(root.iter("team"), start=1):
        is_user = _text(t, "is_owned_by_current_login") == "1" or any(
            _text(m, "is_current_login") == "1" for m in t.iter("manager"))
        draft_pos = _text(t, "draft_position")
        slot = int(draft_pos) if draft_pos.isdigit() else int(_text(t, "team_id", str(i)) or i)
        out.append(Team(slot=slot, name=_text(t, "name") or f"Team {i}", team_key=_text(t, "team_key") or None,
                        is_user=is_user))
    return out


def parse_user_team_keys(root: ET.Element) -> list:
    """users;use_login=1/games;game_keys=nfl/teams -> [team_key]"""
    return [_text(t, "team_key") for t in root.iter("team") if _text(t, "team_key")]


def player_id_from_key(player_key: str) -> str:
    return player_key.rsplit(".", 1)[-1] if player_key else ""


def parse_draft_results(root: ET.Element) -> list:
    """league/{key}/draftresults -> [{pick, round, team_key, player_key, yahoo_player_id}] (completed only)."""
    out = []
    for dr in root.iter("draft_result"):
        pk = _text(dr, "player_key")
        pick = _text(dr, "pick")
        if not pk or not pick.isdigit():
            continue
        out.append({
            "pick": int(pick),
            "round": int(_text(dr, "round", "0") or 0),
            "team_key": _text(dr, "team_key"),
            "player_key": pk,
            "yahoo_player_id": player_id_from_key(pk),
        })
    out.sort(key=lambda d: d["pick"])
    return out


def parse_players(root: ET.Element) -> list:
    """league/{key}/players[/draft_analysis] -> [{yahoo_player_id, name, team, position, adp, percent_drafted, bye}]"""
    out = []
    for p in root.iter("player"):
        pid = _text(p, "player_id") or player_id_from_key(_text(p, "player_key"))
        if not pid:
            continue
        pos = normalize_position(_text(p, "display_position").split(",")[0])
        name = _text(p, "name/full")
        team = normalize_team(_text(p, "editorial_team_abbr"))
        da = p.find("draft_analysis")
        adp = _text(da, "average_pick") if da is not None else ""
        pct = _text(da, "percent_drafted") if da is not None else ""
        bye = _text(p, "bye_weeks/week")
        out.append({
            "yahoo_player_id": pid,
            "name": name,
            "team": team,
            "position": pos,
            "adp": float(adp) if adp not in ("", "-") else None,
            "percent_drafted": float(pct) if pct not in ("", "-") else None,
            "bye": int(bye) if bye.isdigit() else None,
        })
    return out


# --------------------------------------------------------------------------- #
# High-level fetches
# --------------------------------------------------------------------------- #


def fetch_leagues(client: YahooClient) -> list:
    return parse_leagues(client.get_xml("users;use_login=1/games;game_keys=nfl/leagues"))


def fetch_settings(client: YahooClient, league_key: str) -> LeagueSettings:
    return parse_settings(client.get_xml(f"league/{league_key}/settings"))


def fetch_draft_status(client: YahooClient, league_key: str) -> str:
    return parse_draft_status(client.get_xml(f"league/{league_key}"))


def fetch_teams(client: YahooClient, league_key: str) -> list:
    return parse_teams(client.get_xml(f"league/{league_key}/teams"))


def fetch_user_team_keys(client: YahooClient) -> list:
    return parse_user_team_keys(client.get_xml("users;use_login=1/games;game_keys=nfl/teams"))


def fetch_draft_results(client: YahooClient, league_key: str) -> list:
    return parse_draft_results(client.get_xml(f"league/{league_key}/draftresults"))


def fetch_all_players(client: YahooClient, league_key: str, max_players: int = 600,
                      progress=None) -> list:
    """Page through the league's player pool sorted by Yahoo's average draft rank, with ADP."""
    out, start = [], 0
    while start < max_players:
        root = client.get_xml(f"league/{league_key}/players;sort=AR;start={start};count={PAGE_SIZE}/draft_analysis")
        page = parse_players(root)
        out.extend(page)
        if progress:
            progress(len(out))
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


# --------------------------------------------------------------------------- #
# Yahoo player cache + mapping (spec §13)
# --------------------------------------------------------------------------- #

_YAHOO_CACHE_FIELDS = ["yahoo_player_id", "name", "team", "position", "adp", "percent_drafted", "bye"]


def save_yahoo_players(rows: Iterable[dict], path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_YAHOO_CACHE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in _YAHOO_CACHE_FIELDS})


def load_yahoo_players(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            out.append({
                "yahoo_player_id": r["yahoo_player_id"],
                "name": r["name"],
                "team": normalize_team(r["team"]),
                "position": normalize_position(r["position"]),
                "adp": float(r["adp"]) if r.get("adp") else None,
                "percent_drafted": float(r["percent_drafted"]) if r.get("percent_drafted") else None,
                "bye": int(r["bye"]) if r.get("bye") else None,
            })
    return out


def build_mappings(players: Iterable[Player], yahoo_rows: Iterable[dict], manual: Optional[dict] = None) -> dict:
    """Resolve yahoo_player_id for each local player.

    Priority: manual mapping file > exact normalised name+team+position > name+position >
    (DEF only) team+position.  No fuzzy matching.  Returns
    {"mapping": {player_id: yahoo_id}, "unmatched": [Player], "matched": n}.
    """
    manual = manual or {}
    by_ntp: dict = {}
    by_np: dict = {}
    by_tp: dict = {}
    for r in yahoo_rows:
        key = normalize_name(r["name"])
        by_ntp.setdefault((key, r["team"], r["position"]), r)
        by_np.setdefault((key, r["position"]), []).append(r)
        by_tp.setdefault((r["team"], r["position"]), r)
    mapping, unmatched = {}, []
    for p in players:
        if p.player_id in manual:
            mapping[p.player_id] = manual[p.player_id]
            continue
        if p.yahoo_player_id:
            mapping[p.player_id] = p.yahoo_player_id
            continue
        key = normalize_name(p.name)
        hit = by_ntp.get((key, p.team, p.position))
        if hit is None:
            cands = by_np.get((key, p.position), [])
            if len(cands) == 1:
                hit = cands[0]
        if hit is None and p.position == "DEF" and p.team:
            hit = by_tp.get((p.team, "DEF"))
        if hit is None:
            unmatched.append(p)
        else:
            mapping[p.player_id] = hit["yahoo_player_id"]
    return {"mapping": mapping, "unmatched": unmatched, "matched": len(mapping)}


def fill_adp_from_yahoo(players: Iterable[Player], yahoo_rows: Iterable[dict], overwrite: bool = False) -> int:
    """Copy Yahoo average_pick into Player.adp for mapped players (fills gaps unless overwrite)."""
    by_id = {r["yahoo_player_id"]: r for r in yahoo_rows}
    n = 0
    for p in players:
        r = by_id.get(p.yahoo_player_id or "")
        if r and r.get("adp") is not None and (overwrite or p.adp is None):
            p.adp = float(r["adp"])
            if r.get("bye") and not p.bye:
                p.bye = r["bye"]
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Draft results -> DraftPicks
# --------------------------------------------------------------------------- #


def assign_draft_slots(teams: list, results: list, num_teams: int) -> dict:
    """Infer each team's snake slot from round-1 picks; returns {team_key: slot}."""
    slots = {}
    for r in results:
        if r["pick"] <= num_teams and r["team_key"]:
            slots[r["team_key"]] = r["pick"]
    if slots:
        for t in teams:
            if t.team_key in slots:
                t.slot = slots[t.team_key]
    return slots


def draft_picks_from_results(results: list, teams: list, yahoo_to_local: dict, num_teams: int,
                             yahoo_names: Optional[dict] = None) -> list:
    """Translate Yahoo draft results into DraftPick objects.

    Unmapped players get the placeholder id ``yahoo:<id>`` so the pick is still recorded
    (and the readiness page can flag it) without touching the local pool.
    """
    slot_by_key = {t.team_key: t.slot for t in teams if t.team_key}
    names = yahoo_names or {}
    out = []
    for r in results:
        yid = r["yahoo_player_id"]
        local_id = yahoo_to_local.get(yid)
        slot = slot_by_key.get(r["team_key"]) or snake_slot_for_pick(r["pick"], num_teams)
        out.append(DraftPick(
            pick=r["pick"], round=r["round"] or (r["pick"] - 1) // num_teams + 1, slot=slot,
            player_id=local_id or f"yahoo:{yid}", source="yahoo", confirmed=True,
            team_key=r["team_key"], player_name=names.get(yid) or (local_id and None),
            yahoo_player_id=yid,
        ))
    return out
