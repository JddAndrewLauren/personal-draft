"""Core data structures, draft state, and data loading for the draft optimizer.

Nothing in this module knows about Yahoo's API or Streamlit.  The optimizer
(optimizer.py) works purely on the dataclasses defined here.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

TEAM_ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
    "LA": "LAR",
    "LVR": "LV",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",
    "GNB": "GB",
    "KAN": "KC",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TAM": "TB",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "FA": "",
    "": "",
}

POSITION_ALIASES = {
    "DST": "DEF",
    "D/ST": "DEF",
    "DEF": "DEF",
    "D": "DEF",
    "PK": "K",
    "K": "K",
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
}


def normalize_name(name: str) -> str:
    """Normalise a player name so that "D.J. Moore" == "DJ Moore" == "dj moore jr."."""
    if name is None:
        return ""
    s = str(name).lower()
    s = re.sub(r"[.'’`\"-]", "", s)          # punctuation, apostrophes, periods
    s = re.sub(r"[^a-z0-9 ]", " ", s)         # anything else -> space
    parts = [p for p in s.split() if p and p not in SUFFIXES]
    return " ".join(parts)


def normalize_team(team: Optional[str]) -> str:
    if team is None:
        return ""
    t = str(team).strip().upper()
    return TEAM_ALIASES.get(t, t)


def normalize_position(pos: Optional[str]) -> str:
    """"RB12" -> "RB", "DST" -> "DEF", "pk" -> "K"."""
    if pos is None:
        return ""
    p = re.sub(r"\d+$", "", str(pos).strip().upper())
    return POSITION_ALIASES.get(p, p)


def make_player_id(name: str, position: str) -> str:
    return f"{normalize_name(name)}|{normalize_position(position)}"


DEF_NOISE_WORDS = {"d", "st", "dst", "def", "defense", "special", "teams"}


def resolve_defense(players, name: str, team: Optional[str] = None):
    """Match a team defense however the source spells it: "Texans", "Houston Texans",
    "Texans D/ST", "HOU DST" -> the DEF row whose team abbrev or nickname agrees."""
    defs = [p for p in players if p.position == "DEF"]
    words = [w for w in normalize_name(name).split() if w not in DEF_NOISE_WORDS]
    t = normalize_team(team) if team else ""
    if not t and len(words) == 1 and len(words[0]) <= 3:
        t = normalize_team(words[0])
    if t:
        for p in defs:
            if normalize_team(p.team) == t:
                return p
    for p in defs:
        nick = normalize_name(p.name).split()
        if nick and words and nick[-1] == words[-1]:
            return p
    return None


def resolve_player(players, name: str, position: str, team: Optional[str] = None):
    """Find a Player by name + position (and NFL team when ids are disambiguated), else None."""
    if normalize_position(position) == "DEF":
        return resolve_defense(players, name, team)
    pid = make_player_id(name, position)
    by_id = {p.player_id: p for p in players}
    if pid in by_id:
        return by_id[pid]
    cands = [p for p in players if p.player_id.startswith(pid + "|")]   # name|pos|team ids
    if team:
        t = str(team).strip().upper()
        for p in cands:
            if (p.team or "").upper() == t:
                return p
    return cands[0] if cands else None


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #

RISK_UNKNOWN = "—"


@dataclass
class Player:
    player_id: str
    name: str
    team: str
    position: str
    projected_points: float
    adp: Optional[float] = None
    adp_stddev: Optional[float] = None
    yahoo_player_id: Optional[str] = None
    bye: Optional[int] = None

    # External (non-Yahoo) data, e.g. FantasyPros
    external_vbd: Optional[float] = None
    external_adp: Optional[float] = None
    rank_avg: Optional[float] = None
    rank_best: Optional[float] = None
    rank_worst: Optional[float] = None
    rank_stddev: Optional[float] = None
    external_tier: Optional[int] = None
    rank_scope: str = "overall"     # "overall" | "position" (what rank_avg is relative to)

    # Filled by the optimizer
    tier: int = 0
    vor: float = 0.0
    value: float = 0.0
    external_vbd_scaled: Optional[float] = None
    risk_label: str = RISK_UNKNOWN
    risk_score: Optional[float] = None

    @property
    def has_rank_data(self) -> bool:
        return self.rank_stddev is not None


@dataclass
class DraftPick:
    pick: int
    round: int
    slot: int
    player_id: str
    source: str = "manual"          # "manual" | "yahoo"
    confirmed: bool = False         # True once Yahoo reports the same pick
    team_key: Optional[str] = None
    player_name: Optional[str] = None
    yahoo_player_id: Optional[str] = None


@dataclass
class Team:
    slot: int
    name: str
    team_key: Optional[str] = None
    is_user: bool = False


@dataclass
class RosterConfig:
    """Roster slots per position. FLEX is shared among ``flex_positions``; BN is bench."""

    slots: dict = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BN": 6,
    })
    flex_positions: tuple = ("RB", "WR", "TE")
    max_per_position: dict = field(default_factory=lambda: {"QB": 2, "TE": 2, "K": 1, "DEF": 1})

    def starters(self, position: str) -> int:
        return int(self.slots.get(position, 0))

    @property
    def flex_slots(self) -> int:
        return int(self.slots.get("FLEX", 0))

    @property
    def bench_slots(self) -> int:
        return int(self.slots.get("BN", 0))

    @property
    def total_slots(self) -> int:
        return int(sum(self.slots.values()))

    @property
    def starter_positions(self) -> list:
        return [p for p in self.slots if p not in ("FLEX", "BN") and self.slots[p] > 0]

    def total_starters(self) -> int:
        return sum(v for k, v in self.slots.items() if k != "BN")


@dataclass
class LeagueSettings:
    num_teams: int = 12
    rounds: int = 15
    roster: RosterConfig = field(default_factory=RosterConfig)
    scoring: dict = field(default_factory=dict)   # yahoo stat_id -> points
    league_key: Optional[str] = None
    name: str = "Local league"

    @property
    def total_picks(self) -> int:
        return self.num_teams * self.rounds


@dataclass
class SyncConflict:
    pick: int
    local_player_id: str
    yahoo_player_id: str
    local_player_name: Optional[str] = None
    yahoo_player_name: Optional[str] = None
    source: str = "yahoo"        # where the remote pick came from: "yahoo" | "scrape"


@dataclass
class Recommendation:
    player: Player
    score: float                 # value + λ * wait_cost
    adjusted_score: float        # score × roster_need (availability is informational only)
    value: float
    vor: float
    survival: float              # P(available at the user's following pick | available at my pick)
    availability: float          # P(available at my pick | available now) — 1.0 when on the clock
    wait_cost: float
    expected_alternative_value: float
    alternative_name: Optional[str]
    alternative_probability: float
    roster_need: float
    reasons: list = field(default_factory=list)
    confidence: str = ""         # STRONG | MODERATE | CLOSE
    action: str = ""             # TAKE NOW | WAIT | ...


# --------------------------------------------------------------------------- #
# Draft state
# --------------------------------------------------------------------------- #


def snake_slot_for_pick(pick: int, num_teams: int) -> int:
    """Return the draft slot (1-based) that owns overall pick number ``pick``."""
    r = (pick - 1) // num_teams
    i = (pick - 1) % num_teams
    return i + 1 if r % 2 == 0 else num_teams - i


@dataclass
class DraftState:
    settings: LeagueSettings = field(default_factory=LeagueSettings)
    teams: list = field(default_factory=list)
    user_slot: int = 1
    picks: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    last_sync: Optional[float] = None
    sync_status: str = "manual"    # manual | connected | lost
    sync_message: str = ""

    # ---- snake helpers -------------------------------------------------- #
    @property
    def num_teams(self) -> int:
        return self.settings.num_teams

    @property
    def total_picks(self) -> int:
        return self.settings.total_picks

    def slot_for_pick(self, pick: int) -> int:
        return snake_slot_for_pick(pick, self.num_teams)

    def round_of(self, pick: int) -> int:
        return (pick - 1) // self.num_teams + 1

    @property
    def current_pick(self) -> int:
        if not self.picks:
            return 1
        return max(p.pick for p in self.picks) + 1

    @property
    def current_round(self) -> int:
        return self.round_of(min(self.current_pick, self.total_picks))

    @property
    def is_complete(self) -> bool:
        return self.current_pick > self.total_picks

    def team_for_slot(self, slot: int) -> Optional[Team]:
        for t in self.teams:
            if t.slot == slot:
                return t
        return None

    def team_name(self, slot: int) -> str:
        t = self.team_for_slot(slot)
        return t.name if t else f"Team {slot}"

    def user_picks(self) -> list:
        return [n for n in range(1, self.total_picks + 1) if self.slot_for_pick(n) == self.user_slot]

    def next_user_pick(self) -> Optional[int]:
        cur = self.current_pick
        for n in self.user_picks():
            if n >= cur:
                return n
        return None

    def following_user_pick(self) -> Optional[int]:
        nxt = self.next_user_pick()
        if nxt is None:
            return None
        for n in self.user_picks():
            if n > nxt:
                return n
        return None

    @property
    def on_the_clock(self) -> bool:
        return (not self.is_complete) and self.slot_for_pick(self.current_pick) == self.user_slot

    @property
    def picks_until_user(self) -> Optional[int]:
        nxt = self.next_user_pick()
        return None if nxt is None else nxt - self.current_pick

    def user_picks_remaining(self) -> int:
        cur = self.current_pick
        return sum(1 for n in self.user_picks() if n >= cur)

    # ---- picks ------------------------------------------------------------ #
    def pick_by_number(self, pick: int) -> Optional[DraftPick]:
        for p in self.picks:
            if p.pick == pick:
                return p
        return None

    def drafted_ids(self) -> set:
        return {p.player_id for p in self.picks}

    def roster_for(self, slot: int) -> list:
        return [p.player_id for p in sorted(self.picks, key=lambda x: x.pick) if p.slot == slot]

    def user_roster_ids(self) -> list:
        return self.roster_for(self.user_slot)

    def add_pick(self, player_id: str, slot: Optional[int] = None, pick: Optional[int] = None,
                 source: str = "manual", player_name: Optional[str] = None,
                 team_key: Optional[str] = None, yahoo_player_id: Optional[str] = None,
                 confirmed: bool = False) -> DraftPick:
        if pick is None:
            pick = self.current_pick
        if pick < 1 or pick > self.total_picks:
            raise ValueError(f"Pick {pick} is outside the draft (1..{self.total_picks})")
        if self.pick_by_number(pick) is not None:
            raise ValueError(f"Pick {pick} has already been made")
        if player_id in self.drafted_ids():
            raise ValueError(f"{player_name or player_id} has already been drafted")
        if slot is None:
            slot = self.slot_for_pick(pick)
        dp = DraftPick(pick=pick, round=self.round_of(pick), slot=slot, player_id=player_id,
                       source=source, confirmed=confirmed, team_key=team_key,
                       player_name=player_name, yahoo_player_id=yahoo_player_id)
        self.picks.append(dp)
        self.picks.sort(key=lambda x: x.pick)
        return dp

    def undo_last(self) -> Optional[DraftPick]:
        if not self.picks:
            return None
        self.picks.sort(key=lambda x: x.pick)
        last = self.picks.pop()
        self.conflicts = [c for c in self.conflicts if c.pick != last.pick]
        return last

    def reset(self) -> None:
        self.picks = []
        self.conflicts = []

    def last_positions(self, players_by_id: dict, n: int = 6) -> list:
        """Positions of the last ``n`` picks (for run detection display)."""
        out = []
        for p in sorted(self.picks, key=lambda x: x.pick)[-n:]:
            pl = players_by_id.get(p.player_id)
            out.append(pl.position if pl else "?")
        return out

    # ---- Yahoo merge ------------------------------------------------------ #
    def merge_yahoo(self, yahoo_picks: Iterable[DraftPick], source: str = "yahoo") -> tuple:
        """Merge picks reported by a remote source (Yahoo API or draft-room scrape) into local state.

        * Pick numbers the local state has not seen are appended (source=``source``).
        * A local pick with the same player is marked confirmed.
        * A local pick with a different player becomes a SyncConflict; local state
          is never overwritten silently.

        Returns (new_picks, new_conflicts).
        """
        new_picks, new_conflicts = [], []
        existing_conflicts = {c.pick for c in self.conflicts}
        for yp in sorted(yahoo_picks, key=lambda x: x.pick):
            local = self.pick_by_number(yp.pick)
            if local is None:
                if yp.player_id in self.drafted_ids():
                    # Same player recorded locally under a different pick number:
                    # treat as a conflict on the local pick.
                    lp = next(p for p in self.picks if p.player_id == yp.player_id)
                    if lp.pick not in existing_conflicts:
                        c = SyncConflict(pick=lp.pick, local_player_id=lp.player_id,
                                         yahoo_player_id="(pick %d)" % yp.pick,
                                         local_player_name=lp.player_name,
                                         yahoo_player_name=f"{yp.player_name} reported at pick {yp.pick}",
                                         source=source)
                        self.conflicts.append(c)
                        new_conflicts.append(c)
                        existing_conflicts.add(lp.pick)
                    continue
                dp = DraftPick(pick=yp.pick, round=self.round_of(yp.pick), slot=yp.slot,
                               player_id=yp.player_id, source=source, confirmed=True,
                               team_key=yp.team_key, player_name=yp.player_name,
                               yahoo_player_id=yp.yahoo_player_id)
                self.picks.append(dp)
                new_picks.append(dp)
            elif local.player_id == yp.player_id:
                local.confirmed = True
                if yp.team_key and not local.team_key:
                    local.team_key = yp.team_key
                if yp.yahoo_player_id and not local.yahoo_player_id:
                    local.yahoo_player_id = yp.yahoo_player_id
            else:
                if yp.pick not in existing_conflicts:
                    c = SyncConflict(pick=yp.pick, local_player_id=local.player_id,
                                     yahoo_player_id=yp.player_id,
                                     local_player_name=local.player_name,
                                     yahoo_player_name=yp.player_name, source=source)
                    self.conflicts.append(c)
                    new_conflicts.append(c)
                    existing_conflicts.add(yp.pick)
        self.picks.sort(key=lambda x: x.pick)
        return new_picks, new_conflicts

    def resolve_conflict(self, pick: int, keep: str, players_by_id: Optional[dict] = None) -> None:
        conflict = next((c for c in self.conflicts if c.pick == pick), None)
        if conflict is None:
            return
        if keep == "yahoo" and not conflict.yahoo_player_id.startswith("("):
            local = self.pick_by_number(pick)
            if local is not None:
                local.player_id = conflict.yahoo_player_id
                local.player_name = conflict.yahoo_player_name
                local.source = conflict.source
                local.confirmed = True
        elif keep == "local":
            local = self.pick_by_number(pick)
            if local is not None:
                local.confirmed = True
        self.conflicts = [c for c in self.conflicts if c.pick != pick]

    # ---- persistence ------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "settings": {
                "num_teams": self.settings.num_teams,
                "rounds": self.settings.rounds,
                "roster": {
                    "slots": dict(self.settings.roster.slots),
                    "flex_positions": list(self.settings.roster.flex_positions),
                    "max_per_position": dict(self.settings.roster.max_per_position),
                },
                "scoring": dict(self.settings.scoring),
                "league_key": self.settings.league_key,
                "name": self.settings.name,
            },
            "teams": [asdict(t) for t in self.teams],
            "user_slot": self.user_slot,
            "picks": [asdict(p) for p in self.picks],
            "conflicts": [asdict(c) for c in self.conflicts],
            "last_sync": self.last_sync,
            "sync_status": self.sync_status,
            "sync_message": self.sync_message,
            "saved_at": time.time(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DraftState":
        s = d.get("settings", {})
        r = s.get("roster", {})
        roster = RosterConfig(
            slots=dict(r.get("slots", RosterConfig().slots)),
            flex_positions=tuple(r.get("flex_positions", ("RB", "WR", "TE"))),
            max_per_position=dict(r.get("max_per_position", RosterConfig().max_per_position)),
        )
        settings = LeagueSettings(
            num_teams=int(s.get("num_teams", 12)),
            rounds=int(s.get("rounds", 15)),
            roster=roster,
            scoring=dict(s.get("scoring", {})),
            league_key=s.get("league_key"),
            name=s.get("name", "Local league"),
        )
        state = cls(
            settings=settings,
            teams=[Team(**t) for t in d.get("teams", [])],
            user_slot=int(d.get("user_slot", 1)),
            picks=[DraftPick(**p) for p in d.get("picks", [])],
            conflicts=[SyncConflict(**c) for c in d.get("conflicts", [])],
            last_sync=d.get("last_sync"),
            sync_status=d.get("sync_status", "manual"),
            sync_message=d.get("sync_message", ""),
        )
        state.picks.sort(key=lambda x: x.pick)
        return state

    def save(self, path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, path) -> "DraftState":
        return cls.from_dict(json.loads(Path(path).read_text()))


def default_teams(num_teams: int, user_slot: int) -> list:
    return [Team(slot=i, name=f"Team {i}", is_user=(i == user_slot)) for i in range(1, num_teams + 1)]


# --------------------------------------------------------------------------- #
# Configuration (config.yaml)
# --------------------------------------------------------------------------- #

DEFAULT_PATHS = {
    "players_csv": "data/players.csv",
    "mappings_csv": "data/player_mappings.csv",
    "yahoo_players_csv": "data/yahoo_players.csv",
    "draft_state": "draft_state.json",
    "snapshots_dir": "snapshots",
    "log_file": "fantasy-draft.log",
    "token_file": ".yahoo_token.json",
}


def load_config(path="config.yaml") -> dict:
    """Load config.yaml (missing file -> {} with defaults filled in)."""
    p = Path(path)
    cfg: dict = {}
    if p.exists():
        import yaml  # local import keeps models importable without pyyaml in tests
        cfg = yaml.safe_load(p.read_text()) or {}
    cfg.setdefault("league", {})
    cfg.setdefault("draft", {})
    cfg.setdefault("optimizer", {})
    cfg.setdefault("polling", {})
    cfg.setdefault("import", {})
    paths = dict(DEFAULT_PATHS)
    paths.update(cfg.get("paths") or {})
    cfg["paths"] = paths
    return cfg


def settings_from_config(cfg: dict) -> LeagueSettings:
    league = cfg.get("league", {}) or {}
    draft = cfg.get("draft", {}) or {}
    roster_slots = league.get("roster") or RosterConfig().slots
    roster = RosterConfig(
        slots={str(k): int(v) for k, v in roster_slots.items()},
        flex_positions=tuple(league.get("flex_positions") or ("RB", "WR", "TE")),
        max_per_position={str(k): int(v) for k, v in (league.get("max_per_position") or RosterConfig().max_per_position).items()},
    )
    rounds = int(draft.get("rounds") or roster.total_slots)
    reconcile_rounds(roster, rounds)
    scoring = {str(k): float(v) for k, v in (league.get("scoring") or {}).items()}
    return LeagueSettings(num_teams=int(league.get("teams", 12)), rounds=rounds, roster=roster,
                          scoring=scoring, name=str(league.get("name", "Local league")))


def reconcile_rounds(roster: RosterConfig, rounds: int) -> None:
    """Keep bench size consistent with the number of draft rounds (roster slots == rounds),
    so roster-need logic never thinks the roster is full early or has phantom slots."""
    diff = rounds - roster.total_slots
    if diff:
        roster.slots["BN"] = max(0, roster.bench_slots + diff)


# --------------------------------------------------------------------------- #
# Player CSV loading
# --------------------------------------------------------------------------- #

# Column aliases (lower-cased, punctuation stripped) -> canonical field.
_COLUMN_ALIASES = {
    "name": {"player", "playername", "name", "player name"},
    "position": {"position", "pos"},
    "team": {"team", "tm", "nflteam"},
    "points": {"projectedpoints", "projected points", "fpts", "points", "proj", "projection", "pts", "fantasypoints"},
    "adp": {"yahooadp", "yahoo adp", "adp", "averagepick", "avgpick"},
    "adp_stddev": {"adpstddev", "adpstd", "adpsd", "adp stddev"},
    "yahoo_player_id": {"yahooplayerid", "yahoo player id", "yahooid", "playerkey", "yahoo_player_id"},
    "bye": {"bye", "byeweek"},
    "external_vbd": {"vbd", "value", "externalvbd", "vor"},
    "external_adp": {"externaladp", "ecradp", "fpadp", "consensusadp"},
    "rank_avg": {"rankavg", "avg", "ecr", "avgrank", "rank"},
    "rank_best": {"rankbest", "best"},
    "rank_worst": {"rankworst", "worst"},
    "rank_stddev": {"rankstddev", "stddev", "std dev", "sd", "rankstd"},
    "external_tier": {"externaltier", "tiers", "tier"},
}

# Raw stat columns -> Yahoo stat ids (for computing points from stats)
STAT_COLUMN_TO_YAHOO_ID = {
    "passyds": "4", "passtd": "5", "int": "6", "ints": "6", "passint": "6",
    "rushyds": "9", "rushtd": "10",
    "rec": "11", "receptions": "11", "recyds": "12", "rectd": "13",
    "fumlost": "18", "fl": "18", "fumbleslost": "18",
}

DEFAULT_SCORING = {  # Yahoo defaults (half-PPR is the current Yahoo default)
    "4": 0.04, "5": 4.0, "6": -1.0, "9": 0.1, "10": 6.0, "11": 0.5, "12": 0.1, "13": 6.0, "18": -2.0,
}


def _norm_col(col: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(col).strip().lower().replace("_", ""))


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "" or s.lower() in ("nan", "none", "null", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _resolve_columns(header: list) -> dict:
    """Map canonical field -> actual column name for the first matching alias."""
    normed = {_norm_col(h): h for h in header}
    out = {}
    for canon, aliases in _COLUMN_ALIASES.items():
        for a in aliases:
            if a in normed and normed[a] not in out.values():
                out[canon] = normed[a]
                break
    return out


def points_from_stats(row: dict, scoring: dict) -> Optional[float]:
    """Compute fantasy points from raw stat columns using a Yahoo stat_id -> points map."""
    total, found = 0.0, False
    for col, val in row.items():
        sid = STAT_COLUMN_TO_YAHOO_ID.get(_norm_col(col))
        if sid is None:
            continue
        v = _to_float(val)
        if v is None:
            continue
        found = True
        total += v * float(scoring.get(sid, 0.0))
    return round(total, 2) if found else None


def _read_rows(source) -> list:
    """Accept a path, a file-like object, or raw CSV text and return list-of-dicts."""
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig")
        text = data
    else:
        p = Path(str(source))
        if p.exists():
            text = p.read_text(encoding="utf-8-sig")
        else:
            text = str(source)
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return [dict(r) for r in reader], list(reader.fieldnames or [])


def load_players(source, scoring: Optional[dict] = None) -> list:
    """Load the master player table from a CSV (spec §11/§12).

    Required: name, position, team, and either projected points or raw stat columns.
    Optional: ADP, ADP std dev, Yahoo player id, external VBD / rank columns.
    """
    rows, header = _read_rows(source)
    cols = _resolve_columns(header)
    if "name" not in cols or "position" not in cols:
        raise ValueError(f"CSV must contain player name and position columns; found {header}")
    scoring = scoring or DEFAULT_SCORING
    players: list = []
    seen: dict = {}
    for r in rows:
        name = (r.get(cols["name"]) or "").strip()
        if not name:
            continue
        pos = normalize_position(r.get(cols["position"]))
        team = normalize_team(r.get(cols.get("team", ""), "")) if "team" in cols else ""
        pts = _to_float(r.get(cols["points"])) if "points" in cols else None
        if pts is None:
            pts = points_from_stats(r, scoring)
        if pts is None:
            pts = 0.0
        pid = make_player_id(name, pos)
        if pid in seen:
            # Same name+position twice (e.g. two "Josh Allen"): disambiguate by team.
            other = seen[pid]
            if other.team != team:
                other_new = f"{pid}|{other.team}"
                other.player_id = other_new
                seen[other_new] = other
                pid = f"{pid}|{team}"
            else:
                continue  # true duplicate row
        p = Player(
            player_id=pid, name=name, team=team, position=pos, projected_points=pts,
            adp=_to_float(r.get(cols["adp"])) if "adp" in cols else None,
            adp_stddev=_to_float(r.get(cols["adp_stddev"])) if "adp_stddev" in cols else None,
            yahoo_player_id=(str(r.get(cols["yahoo_player_id"])).strip() or None) if "yahoo_player_id" in cols else None,
            bye=int(_to_float(r.get(cols["bye"])) or 0) or None if "bye" in cols else None,
            external_vbd=_to_float(r.get(cols["external_vbd"])) if "external_vbd" in cols else None,
            external_adp=_to_float(r.get(cols["external_adp"])) if "external_adp" in cols else None,
            rank_avg=_to_float(r.get(cols["rank_avg"])) if "rank_avg" in cols else None,
            rank_best=_to_float(r.get(cols["rank_best"])) if "rank_best" in cols else None,
            rank_worst=_to_float(r.get(cols["rank_worst"])) if "rank_worst" in cols else None,
            rank_stddev=_to_float(r.get(cols["rank_stddev"])) if "rank_stddev" in cols else None,
            external_tier=int(_to_float(r.get(cols["external_tier"])) or 0) or None if "external_tier" in cols else None,
        )
        if p.yahoo_player_id in ("None", "nan", ""):
            p.yahoo_player_id = None
        seen[pid] = p
        players.append(p)
    return players


def players_by_id(players: Iterable[Player]) -> dict:
    return {p.player_id: p for p in players}


# --------------------------------------------------------------------------- #
# External data import (FantasyPros exports, generic column-mapped CSV)
# --------------------------------------------------------------------------- #

FORMAT_FP_ECR = "fantasypros_ecr"
FORMAT_FP_PROJECTIONS = "fantasypros_projections"
FORMAT_GENERIC = "generic"


def detect_format(header: list) -> str:
    normed = {_norm_col(h) for h in header}
    if {"rk", "player name"} <= normed or {"rk", "playername"} <= normed:
        return FORMAT_FP_ECR
    if "fpts" in normed:
        return FORMAT_FP_PROJECTIONS
    return FORMAT_GENERIC


def read_external(source, fmt: Optional[str] = None, column_map: Optional[dict] = None,
                  position: Optional[str] = None, scoring: Optional[dict] = None) -> list:
    """Read an external CSV into a list of row dicts with canonical keys.

    Canonical keys: name, team, position, points, vbd, adp, rank, rank_avg, rank_best,
    rank_worst, rank_stddev, tier.  Missing values are None.
    """
    rows, header = _read_rows(source)
    fmt = fmt or detect_format(header)
    out = []
    if fmt == FORMAT_FP_ECR:
        for r in rows:
            g = {_norm_col(k): v for k, v in r.items()}
            name = (g.get("player name") or g.get("playername") or "").strip()
            if not name:
                continue
            pos = normalize_position(g.get("pos") or position)
            out.append({
                "name": name,
                "team": normalize_team(g.get("team")),
                "position": pos,
                "points": None,
                "vbd": _to_float(g.get("vbd")),
                "adp": _to_float(g.get("adp")),
                "rank": _to_float(g.get("rk")),
                "rank_avg": _to_float(g.get("avg")),
                "rank_best": _to_float(g.get("best")),
                "rank_worst": _to_float(g.get("worst")),
                "rank_stddev": _to_float(g.get("stddev") or g.get("std dev")),
                "tier": int(_to_float(g.get("tiers") or g.get("tier")) or 0) or None,
                "ecr_vs_adp": _to_float(g.get("ecr vs adp") or g.get("ecrvsadp")),
            })
        # A single-position ECR export carries positional ranks, not overall ranks.
        scope = "position" if len({r["position"] for r in out}) == 1 else "overall"
        for r in out:
            r["rank_scope"] = scope
    elif fmt == FORMAT_FP_PROJECTIONS:
        for r in rows:
            g = {_norm_col(k): v for k, v in r.items()}
            name = (g.get("player") or g.get("player name") or g.get("playername") or "").strip()
            if not name:
                continue
            pos = normalize_position(g.get("pos") or g.get("position") or position)
            pts = _to_float(g.get("fpts"))
            if pts is None and scoring:
                pts = points_from_stats(r, scoring)
            out.append({
                "name": name,
                "team": normalize_team(g.get("team")),
                "position": pos,
                "points": pts,
                "vbd": _to_float(g.get("vbd") or g.get("value")),
                "adp": _to_float(g.get("adp")),
                "rank": None, "rank_avg": None, "rank_best": None, "rank_worst": None,
                "rank_stddev": None, "tier": None, "ecr_vs_adp": None,
            })
    else:
        cmap = column_map or {}
        cols = _resolve_columns(header)

        def col(canon, generic_key):
            return cmap.get(generic_key) or cols.get(canon)

        for r in rows:
            name_col = col("name", "name")
            name = (r.get(name_col) or "").strip() if name_col else ""
            if not name:
                continue
            pos_col = col("position", "position")
            team_col = col("team", "team")
            out.append({
                "name": name,
                "team": normalize_team(r.get(team_col)) if team_col else "",
                "position": normalize_position(r.get(pos_col) if pos_col else position),
                "points": _to_float(r.get(col("points", "points"))) if col("points", "points") else None,
                "vbd": _to_float(r.get(col("external_vbd", "vbd"))) if col("external_vbd", "vbd") else None,
                "adp": _to_float(r.get(col("external_adp", "adp"))) if col("external_adp", "adp") else None,
                "rank": None,
                "rank_avg": _to_float(r.get(col("rank_avg", "rank_avg"))) if col("rank_avg", "rank_avg") else None,
                "rank_best": _to_float(r.get(col("rank_best", "rank_best"))) if col("rank_best", "rank_best") else None,
                "rank_worst": _to_float(r.get(col("rank_worst", "rank_worst"))) if col("rank_worst", "rank_worst") else None,
                "rank_stddev": _to_float(r.get(col("rank_stddev", "rank_stddev"))) if col("rank_stddev", "rank_stddev") else None,
                "tier": (int(_to_float(r.get(col("external_tier", "tier"))) or 0) or None) if col("external_tier", "tier") else None,
                "ecr_vs_adp": None,
            })
    return out


def merge_external(players: list, rows: list, overwrite_points: bool = False) -> dict:
    """Merge external rows into the master player list by normalised name (+position, +team).

    Returns {"matched": n, "unmatched": [row, ...]}.  No fuzzy matching.
    """
    by_name_pos: dict = {}
    by_name: dict = {}
    for p in players:
        by_name_pos.setdefault((normalize_name(p.name), p.position), []).append(p)
        by_name.setdefault(normalize_name(p.name), []).append(p)

    matched, unmatched = 0, []
    for r in rows:
        key = normalize_name(r["name"])
        cands = by_name_pos.get((key, r["position"])) if r.get("position") else None
        if not cands:
            cands = by_name.get(key, [])
        if not cands:
            unmatched.append(r)
            continue
        if len(cands) > 1 and r.get("team"):
            same_team = [c for c in cands if c.team == r["team"]]
            if same_team:
                cands = same_team
        p = cands[0]
        matched += 1
        if r.get("points") is not None and (overwrite_points or not p.projected_points):
            p.projected_points = r["points"]
        for src, dst in (("vbd", "external_vbd"), ("adp", "external_adp"), ("rank_avg", "rank_avg"),
                         ("rank_best", "rank_best"), ("rank_worst", "rank_worst"),
                         ("rank_stddev", "rank_stddev"), ("tier", "external_tier")):
            if r.get(src) is not None:
                setattr(p, dst, r[src])
        if r.get("rank") is not None and p.rank_avg is None:
            p.rank_avg = r["rank"]
        if r.get("rank_scope"):
            p.rank_scope = r["rank_scope"]
        if not p.team and r.get("team"):
            p.team = r["team"]
    return {"matched": matched, "unmatched": unmatched}


def write_players_csv(players: Iterable[Player], path) -> None:
    """Write the (possibly enriched) master table back out in the canonical CSV format."""
    fields = ["Player", "Position", "Team", "ProjectedPoints", "YahooADP", "ADPStdDev", "YahooPlayerID",
              "Bye", "VBD", "ExternalADP", "RankAvg", "RankBest", "RankWorst", "RankStdDev", "ExternalTier"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for p in players:
            w.writerow([p.name, p.position, p.team, p.projected_points, p.adp, p.adp_stddev,
                        p.yahoo_player_id or "", p.bye or "", p.external_vbd, p.external_adp,
                        p.rank_avg, p.rank_best, p.rank_worst, p.rank_stddev, p.external_tier])


def enrich_key(name: str, position: str, team: str = "") -> tuple:
    """Match key for enriching the master CSV: name+position, except defenses, which sources name
    differently ("Texans" vs "Houston Texans") and so match on team."""
    pos = normalize_position(position)
    if pos == "DEF":
        return ("DEF", normalize_team(team))
    return (pos, normalize_name(name))


def enrich_players_csv(path, updates: dict, columns: list) -> dict:
    """Add/overwrite ``columns`` in the master players CSV from ``updates``: {enrich_key: {col: val}}.
    Existing columns and leading ``#`` comment lines are preserved; an update whose key has several
    master rows (same name+position, different teams) may carry a ``_team`` to break the tie.
    Returns {"matched": n, "unmatched_keys": [...]}."""
    path = Path(path)
    lines = path.read_text().splitlines()
    comments = [ln for ln in lines if ln.startswith("#")]
    reader = csv.DictReader([ln for ln in lines if not ln.startswith("#")])
    fields = list(reader.fieldnames or [])
    for col in columns:
        if col not in fields:
            fields.append(col)
    rows = list(reader)
    by_key: dict = {}
    for r in rows:
        by_key.setdefault(enrich_key(r["Player"], r["Position"], r.get("Team", "")), []).append(r)
    matched, used = 0, set()
    for key, upd in updates.items():
        cands = by_key.get(key, [])
        if len(cands) > 1 and upd.get("_team"):
            same = [c for c in cands if normalize_team(c.get("Team")) == normalize_team(upd["_team"])]
            cands = same or cands
        if not cands:
            continue
        matched += 1
        used.add(key)
        for col in columns:
            if col in upd:
                cands[0][col] = upd[col]
    for r in rows:
        for col in columns:
            r.setdefault(col, "")
    with path.open("w", newline="") as f:
        for c in comments:
            f.write(c + "\n")
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return {"matched": matched, "unmatched_keys": [k for k in updates if k not in used]}


# --------------------------------------------------------------------------- #
# Player mappings (local player_id <-> yahoo_player_id), spec §13
# --------------------------------------------------------------------------- #


def load_mappings(path) -> dict:
    """Return {player_id: yahoo_player_id} from data/player_mappings.csv (may not exist)."""
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            pid, yid = (r.get("player_id") or "").strip(), (r.get("yahoo_player_id") or "").strip()
            if pid and yid:
                out[pid] = yid
    return out


def save_mappings(path, mappings: dict, names: Optional[dict] = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "yahoo_player_id", "name"])
        for pid, yid in sorted(mappings.items()):
            w.writerow([pid, yid, (names or {}).get(pid, "")])


def apply_mappings(players: Iterable[Player], mappings: dict) -> None:
    for p in players:
        if p.player_id in mappings:
            p.yahoo_player_id = mappings[p.player_id]
