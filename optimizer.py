"""Draft optimizer: VOR, tiers, survival, wait cost, roster need, ranking, explanations.

Pure Python over the dataclasses in models.py.  Knows nothing about Yahoo or Streamlit.

Pipeline
--------
prepare_players(players, settings, cfg)   # once per data load: VOR, tiers, VBD blend, risk labels
recommend(state, players, cfg)            # per pick: survival, wait cost, roster need, ranking
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from models import (
    DraftState,
    LeagueSettings,
    Player,
    Recommendation,
    RosterConfig,
    RISK_UNKNOWN,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "wait_cost_weight": 1.0,          # λ in score = value + λ·wait_cost
    "adp_stddev": 6.0,                # base σ of a player's draft position (picks)
    "adp_stddev_per_pick": 0.08,      # σ grows with ADP: σ = base + per_pick·ADP
    "flex_weights": {"RB": 0.5, "WR": 0.5, "TE": 0.0},
    "tier_gap_points": {"QB": 12.0, "RB": 10.0, "WR": 10.0, "TE": 10.0, "K": 6.0, "DEF": 6.0, "default": 10.0},
    "tier_width_points": {"QB": 25.0, "RB": 22.0, "WR": 22.0, "TE": 20.0, "K": 10.0, "DEF": 10.0, "default": 20.0},
    "tier_max_players": 48,
    # Projection-reliability discount applied to VOR (kicker / defense projections are mostly noise).
    "position_value_scale": {"K": 0.5, "DEF": 0.5},
    "need_multipliers": {
        "starter_open": 1.10,
        "flex_open": 1.00,
        "bench": 0.85,
        "bench_deep": 0.70,
        "bench_deep_after": 2,        # surplus bodies at a position before "deep" applies
    },
    "confidence_thresholds": {"strong": 8.0, "moderate": 3.0},
    "external_vbd_weight": 0.5,       # w in value = (1-w)·VOR + w·external VBD (scaled)
    "risk_thresholds": {"safe": -0.5, "boom_bust": 0.5},
    "top_n": 5,
}


def merge_config(overrides: Optional[dict]) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


# --------------------------------------------------------------------------- #
# Replacement levels, VOR, tiers (spec §14–16)
# --------------------------------------------------------------------------- #


def replacement_ranks(settings: LeagueSettings, flex_weights: Optional[dict] = None) -> dict:
    """Positional rank of the replacement-level player, derived from the roster config.

    rank = teams × starters + teams × FLEX slots × flex_weight[pos] + 1
    12 teams, QB1/RB2/WR3/TE1/FLEX1 with RB .5 / WR .5 -> QB13, RB31, WR43, TE13.
    """
    fw = flex_weights or DEFAULT_CONFIG["flex_weights"]
    teams, roster = settings.num_teams, settings.roster
    ranks = {}
    for pos in roster.starter_positions:
        flex_share = teams * roster.flex_slots * float(fw.get(pos, 0.0)) if pos in roster.flex_positions else 0.0
        ranks[pos] = int(round(teams * roster.starters(pos) + flex_share)) + 1
    return ranks


def replacement_points(players: Iterable[Player], ranks: dict) -> dict:
    """Projected points of the replacement-level player at each position (full pool)."""
    by_pos: dict = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p.projected_points)
    out = {}
    for pos, pts in by_pos.items():
        pts.sort(reverse=True)
        rank = ranks.get(pos)
        if rank is None:                     # position not rostered: nothing has value
            out[pos] = pts[0] if pts else 0.0
        else:
            out[pos] = pts[min(rank, len(pts)) - 1] if pts else 0.0
    return out


def compute_vor(players: list, settings: LeagueSettings, flex_weights: Optional[dict] = None,
                value_scale: Optional[dict] = None) -> dict:
    ranks = replacement_ranks(settings, flex_weights)
    repl = replacement_points(players, ranks)
    scale = value_scale or {}
    for p in players:
        raw = p.projected_points - repl.get(p.position, 0.0)
        p.vor = round(raw * float(scale.get(p.position, 1.0)), 2)
    return repl


def _gap_for(pos: str, gaps: dict) -> float:
    return float(gaps.get(pos, gaps.get("default", 10.0)))


def assign_tiers(players: list, tier_gaps: Optional[dict] = None, max_players: int = 48,
                 tier_widths: Optional[dict] = None) -> None:
    """Tier = 1 for the best at a position.  A new tier starts when either

    * the projection gap to the previous player is ≥ ``tier_gap_points[pos]`` (a cliff), or
    * the drop from the first player of the current tier is ≥ ``tier_width_points[pos]``
      (keeps tiers meaningful on smooth curves with no obvious cliffs).
    """
    gaps = tier_gaps or DEFAULT_CONFIG["tier_gap_points"]
    widths = tier_widths or DEFAULT_CONFIG["tier_width_points"]
    by_pos: dict = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)
    for pos, group in by_pos.items():
        group.sort(key=lambda x: x.projected_points, reverse=True)
        gap, width = _gap_for(pos, gaps), _gap_for(pos, widths)
        tier, prev, tier_top = 1, None, None
        for i, p in enumerate(group):
            if prev is not None and i < max_players and (
                (prev - p.projected_points) >= gap or (tier_top - p.projected_points) >= width
            ):
                tier += 1
                tier_top = p.projected_points
            if tier_top is None:
                tier_top = p.projected_points
            p.tier = tier
            prev = p.projected_points


# --------------------------------------------------------------------------- #
# External VBD blend and risk labels (plan §3c)
# --------------------------------------------------------------------------- #


def fill_external_vbd_from_ranks(players: list) -> int:
    """Players with an external rank but no external VBD get the VOR of the r-th best player
    on our own VOR curve (overall or positional per ``rank_scope``), so the blend has comparable units.
    Returns the number of players filled."""
    overall = sorted((p.vor for p in players), reverse=True)
    by_pos: dict = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p.vor)
    for v in by_pos.values():
        v.sort(reverse=True)
    n = 0
    for p in players:
        if p.external_vbd is not None or p.rank_avg is None:
            continue
        curve = by_pos[p.position] if p.rank_scope == "position" else overall
        if not curve:
            continue
        idx = min(max(int(round(p.rank_avg)) - 1, 0), len(curve) - 1)
        p.external_vbd = round(curve[idx], 2)
        n += 1
    return n


def _linear_fit(xs: list, ys: list) -> Optional[tuple]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-9:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def blend_value(players: list, weight: float = 0.5, min_pairs: int = 5) -> None:
    """value = (1-w)·VOR + w·external VBD rescaled onto the VOR scale (global linear fit)."""
    pairs = [(p.external_vbd, p.vor) for p in players if p.external_vbd is not None]
    fit = _linear_fit([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= min_pairs else None
    if fit is not None and fit[1] <= 0:
        fit = None
    w = min(max(float(weight), 0.0), 1.0)
    for p in players:
        if p.external_vbd is None:
            p.external_vbd_scaled = None
            p.value = p.vor
            continue
        scaled = fit[0] + fit[1] * p.external_vbd if fit else p.external_vbd
        p.external_vbd_scaled = round(scaled, 2)
        p.value = round((1 - w) * p.vor + w * scaled, 2)


def _zscores(values: list) -> list:
    n = len(values)
    if n < 3:
        return [0.0] * n
    m = sum(values) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    if sd <= 1e-9:
        return [0.0] * n
    return [(v - m) / sd for v in values]


def risk_labels(players: list, thresholds: Optional[dict] = None) -> None:
    """Label SAFE / BALANCED / BOOM-BUST from expert-rank disagreement relative to positional peers.

    Raw signal: rank_stddev / sqrt(rank_avg)  (disagreement naturally grows with rank depth),
    plus 30 % weight on (worst − best) / rank_avg.  Z-scored within position.
    """
    th = thresholds or DEFAULT_CONFIG["risk_thresholds"]
    by_pos: dict = {}
    for p in players:
        p.risk_label, p.risk_score = RISK_UNKNOWN, None
        if p.rank_stddev is None:
            continue
        by_pos.setdefault(p.position, []).append(p)
    for group in by_pos.values():
        base = [g.rank_stddev / math.sqrt(max(g.rank_avg or 1.0, 1.0)) for g in group]
        spread = []
        for g in group:
            if g.rank_best is not None and g.rank_worst is not None:
                spread.append((g.rank_worst - g.rank_best) / max(g.rank_avg or 1.0, 1.0))
            else:
                spread.append(None)
        zb = _zscores(base)
        have_spread = [s for s in spread if s is not None]
        zs_map = {}
        if len(have_spread) >= 3:
            zs = _zscores(have_spread)
            it = iter(zs)
            for i, s in enumerate(spread):
                if s is not None:
                    zs_map[i] = next(it)
        for i, g in enumerate(group):
            z = 0.7 * zb[i] + 0.3 * zs_map.get(i, zb[i])
            g.risk_score = round(z, 2)
            if z <= th["safe"]:
                g.risk_label = "SAFE"
            elif z >= th["boom_bust"]:
                g.risk_label = "BOOM-BUST"
            else:
                g.risk_label = "BALANCED"


def prepare_players(players: list, settings: LeagueSettings, cfg: Optional[dict] = None) -> dict:
    """Run the once-per-load computations. Returns the replacement points per position."""
    cfg = merge_config(cfg)
    repl = compute_vor(players, settings, cfg["flex_weights"], cfg["position_value_scale"])
    assign_tiers(players, cfg["tier_gap_points"], cfg["tier_max_players"], cfg["tier_width_points"])
    fill_external_vbd_from_ranks(players)
    blend_value(players, cfg["external_vbd_weight"])
    risk_labels(players, cfg["risk_thresholds"])
    return repl


# --------------------------------------------------------------------------- #
# Availability model (spec §17–19)
# --------------------------------------------------------------------------- #


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def survival_probability(adp: float, stddev: float, from_pick: int, to_pick: int) -> float:
    """P(draft position ≥ to_pick | draft position ≥ from_pick) under N(adp, σ²).

    "Drafted before pick t" means X < t − 0.5 on the continuous scale.
    """
    if to_pick <= from_pick:
        return 1.0
    sd = max(float(stddev), 0.5)
    # erfc keeps precision deep in the upper tail where 1 - cdf would underflow.
    tail_to = 0.5 * math.erfc((to_pick - 0.5 - adp) / (sd * math.sqrt(2.0)))
    tail_from = 0.5 * math.erfc((from_pick - 0.5 - adp) / (sd * math.sqrt(2.0)))
    if tail_from <= 0.0:
        # Absurdly far past ADP (both tails underflow): the market has plainly discounted
        # him; fall back to a per-pick hazard so the number stays sane.
        return 0.5 ** (to_pick - from_pick)
    return max(0.0, min(1.0, tail_to / tail_from))


def effective_adp(player: Player, total_picks: int) -> float:
    if player.adp is not None:
        return float(player.adp)
    if player.external_adp is not None:
        return float(player.external_adp)
    if player.rank_avg is not None and player.rank_scope == "overall":
        return float(player.rank_avg)
    return float(total_picks + 50)


def effective_stddev(player: Player, adp: float, cfg: dict) -> float:
    if player.adp_stddev is not None:
        return float(player.adp_stddev)
    return float(cfg["adp_stddev"]) + float(cfg["adp_stddev_per_pick"]) * adp


def player_survival(player: Player, from_pick: int, to_pick: int, total_picks: int, cfg: dict) -> float:
    adp = effective_adp(player, total_picks)
    return survival_probability(adp, effective_stddev(player, adp, cfg), from_pick, to_pick)


# --------------------------------------------------------------------------- #
# Expected alternatives and wait cost (spec §20–21)
# --------------------------------------------------------------------------- #


def expected_best(candidates: list) -> tuple:
    """candidates: list of (player, value, survival).  Assuming independence, returns
    (expected best value, most-likely best player, its probability).

    E = Σ v_i · s_i · Π_{j<i}(1 − s_j) over candidates sorted by value desc.
    """
    ordered = sorted(candidates, key=lambda c: c[1], reverse=True)
    expected, remaining = 0.0, 1.0
    best_p, best_prob = None, 0.0
    for player, value, s in ordered:
        prob = remaining * s
        expected += value * prob
        if prob > best_prob:
            best_p, best_prob = player, prob
        remaining *= (1.0 - s)
        if remaining < 1e-9:
            break
    return expected, best_p, best_prob


def wait_cost(value: float, survival: float, expected_alternative: float) -> float:
    return (1.0 - survival) * max(0.0, value - expected_alternative)


def apply_multipliers(score: float, need: float, availability: float) -> float:
    """score × need × availability, kept monotone for negative scores (late-draft, below
    replacement): a higher need must never make a negative score *worse*, so negatives are
    divided by the multipliers instead."""
    m = need * availability
    if m <= 0.0:
        return 0.0
    return score * m if score >= 0 else score / m


# --------------------------------------------------------------------------- #
# Roster need (spec §23–25)
# --------------------------------------------------------------------------- #


def roster_counts(roster_players: Iterable[Player]) -> dict:
    counts: dict = {}
    for p in roster_players:
        counts[p.position] = counts.get(p.position, 0) + 1
    return counts


def open_starter_slots(counts: dict, roster: RosterConfig) -> tuple:
    """Returns (open per position dict, open FLEX slots)."""
    opens = {pos: max(0, roster.starters(pos) - counts.get(pos, 0)) for pos in roster.starter_positions}
    flex_used = sum(max(0, counts.get(pos, 0) - roster.starters(pos)) for pos in roster.flex_positions)
    flex_open = max(0, roster.flex_slots - flex_used)
    return opens, flex_open


@dataclass
class NeedInfo:
    multiplier: float
    reason: str
    fills_starter: bool = False
    fills_flex: bool = False


def roster_need(position: str, counts: dict, roster: RosterConfig, user_picks_remaining: int,
                mult: Optional[dict] = None) -> NeedInfo:
    m = mult or DEFAULT_CONFIG["need_multipliers"]
    have = counts.get(position, 0)
    total_have = sum(counts.values())
    if total_have >= roster.total_slots:
        return NeedInfo(0.0, "roster full")
    cap = roster.max_per_position.get(position)
    if cap is not None and have >= cap:
        return NeedInfo(0.0, f"already at max {cap} {position}")
    if position not in roster.starter_positions and position not in roster.flex_positions:
        return NeedInfo(0.0, f"{position} is not rosterable in this league")

    opens, flex_open = open_starter_slots(counts, roster)
    total_open = sum(opens.values()) + flex_open
    fills_starter = opens.get(position, 0) > 0
    fills_flex = (not fills_starter) and position in roster.flex_positions and flex_open > 0
    if user_picks_remaining <= total_open and not (fills_starter or fills_flex):
        return NeedInfo(0.0, f"only {user_picks_remaining} picks left for {total_open} open starter slots")
    if fills_starter:
        return NeedInfo(float(m["starter_open"]), f"{position} starter slot open", fills_starter=True)
    if fills_flex:
        return NeedInfo(float(m["flex_open"]), "FLEX slot open", fills_flex=True)
    surplus = have - roster.starters(position)
    if surplus >= int(m["bench_deep_after"]):
        return NeedInfo(float(m["bench_deep"]), f"{position} already deep ({have} rostered)")
    return NeedInfo(float(m["bench"]), f"bench depth at {position}")


def assign_roster_slots(roster_players: list, roster: RosterConfig) -> dict:
    """Greedy display assignment of the user's players to slot labels (QB, RB, ..., FLEX, BN)."""
    slots = {pos: [] for pos in roster.slots}
    for p in sorted(roster_players, key=lambda x: x.projected_points, reverse=True):
        if len(slots.get(p.position, [])) < roster.starters(p.position) and p.position in slots:
            slots[p.position].append(p)
        elif p.position in roster.flex_positions and len(slots.get("FLEX", [])) < roster.flex_slots:
            slots.setdefault("FLEX", []).append(p)
        else:
            slots.setdefault("BN", []).append(p)
    return slots


# --------------------------------------------------------------------------- #
# Recommendation (spec §22, §26, §27)
# --------------------------------------------------------------------------- #


@dataclass
class RecommendationContext:
    current_pick: int
    my_pick: int
    following_pick: Optional[int]
    on_the_clock: bool
    picks_remaining: int
    counts: dict = field(default_factory=dict)
    replacement: dict = field(default_factory=dict)
    total_picks: int = 0


def _confidence(top: float, second: Optional[float], th: dict) -> str:
    if second is None:
        return "STRONG"
    gap = top - second
    if gap >= float(th["strong"]):
        return "STRONG"
    if gap >= float(th["moderate"]):
        return "MODERATE"
    return "CLOSE"


def recommend(state: DraftState, players: list, cfg: Optional[dict] = None) -> list:
    """Rank every available player for the user's next pick.  Returns Recommendations sorted desc."""
    cfg = merge_config(cfg)
    lam = float(cfg["wait_cost_weight"])
    drafted = state.drafted_ids()
    by_id = {p.player_id: p for p in players}
    available = [p for p in players if p.player_id not in drafted]
    my_pick = state.next_user_pick()
    if my_pick is None or not available:
        return []
    following = state.following_user_pick()
    current = state.current_pick
    on_clock = state.on_the_clock
    total = state.total_picks
    roster_players = [by_id[pid] for pid in state.user_roster_ids() if pid in by_id]
    counts = roster_counts(roster_players)
    picks_remaining = state.user_picks_remaining()
    roster = state.settings.roster

    # Survival numbers
    avail_now: dict = {}      # P(available at my_pick | available now)
    surv_next: dict = {}      # P(available at following | available at my_pick)
    surv_alt: dict = {}       # P(available at following | available now)   (for alternatives)
    for p in available:
        avail_now[p.player_id] = 1.0 if on_clock else player_survival(p, current, my_pick, total, cfg)
        if following is None:
            surv_next[p.player_id] = 0.0
            surv_alt[p.player_id] = 0.0
        else:
            surv_next[p.player_id] = player_survival(p, my_pick, following, total, cfg)
            surv_alt[p.player_id] = player_survival(p, current, following, total, cfg)

    by_pos: dict = {}
    for p in available:
        by_pos.setdefault(p.position, []).append(p)

    need_cache = {pos: roster_need(pos, counts, roster, picks_remaining, cfg["need_multipliers"])
                  for pos in by_pos}

    recs = []
    for pos, group in by_pos.items():
        need = need_cache[pos]
        # Only the top of the position matters as an alternative; cap for speed.
        group_sorted = sorted(group, key=lambda x: x.value, reverse=True)[:60]
        cands = [(g, g.value, surv_alt[g.player_id]) for g in group_sorted]
        for p in group:
            if following is None:
                exp_alt, alt_p, alt_prob, wc = 0.0, None, 0.0, 0.0
            else:
                others = [c for c in cands if c[0] is not p]
                exp_alt, alt_p, alt_prob = expected_best(others)
                wc = wait_cost(p.value, surv_next[p.player_id], exp_alt)
            score = p.value + lam * wc
            adjusted = apply_multipliers(score, need.multiplier, 1.0 if on_clock else avail_now[p.player_id])
            recs.append(Recommendation(
                player=p, score=round(score, 2), adjusted_score=round(adjusted, 2), value=p.value,
                vor=p.vor, survival=surv_next[p.player_id], availability=avail_now[p.player_id],
                wait_cost=round(wc, 2), expected_alternative_value=round(exp_alt, 2),
                alternative_name=alt_p.name if alt_p else None, alternative_probability=alt_prob,
                roster_need=need.multiplier,
            ))
    # Blocked players (need = 0) always sort last; ties (e.g. two equivalent players)
    # break toward the one less likely to survive.
    recs.sort(key=lambda r: (r.roster_need > 0, r.adjusted_score, -r.survival, r.value), reverse=True)

    ctx = RecommendationContext(current_pick=current, my_pick=my_pick, following_pick=following,
                                on_the_clock=on_clock, picks_remaining=picks_remaining, counts=counts,
                                total_picks=total)
    top_n = int(cfg["top_n"])
    conf = _confidence(recs[0].adjusted_score, recs[1].adjusted_score if len(recs) > 1 else None,
                       cfg["confidence_thresholds"]) if recs else ""
    for i, r in enumerate(recs[:max(top_n, 1)]):
        r.confidence = conf
        r.action = _action(r, i, conf, ctx)
        r.reasons = explain(r, ctx, by_pos, need_cache[r.player.position], cfg)
    return recs


def _action(rec: Recommendation, rank: int, confidence: str, ctx: RecommendationContext) -> str:
    if ctx.following_pick is None:
        return "LAST PICK"
    if rank == 0:
        if confidence == "CLOSE":
            return "CLOSE DECISION"
        # Same thresholds as the "Little urgency" bullet in explain().
        if rec.survival >= 0.8 and rec.wait_cost < 1.0:
            return "SAFE TO WAIT"
        return "TAKE NOW"
    if rec.survival >= 0.8:
        return "LIKELY AVAILABLE LATER"
    if rec.survival <= 0.35:
        return "NOW OR NEVER"
    return "ALTERNATIVE"


def explain(rec: Recommendation, ctx: RecommendationContext, by_pos: dict, need: NeedInfo,
            cfg: dict) -> list:
    """Deterministic explanation bullets built from the optimizer's own numbers (spec §26)."""
    p = rec.player
    out = []
    group = sorted(by_pos.get(p.position, []), key=lambda x: x.value, reverse=True)
    pos_rank = next((i for i, g in enumerate(group) if g is p), 0) + 1

    if pos_rank == 1:
        out.append(f"Highest value available at {p.position} (value {rec.value:.0f}, VOR {rec.vor:.0f}).")
    else:
        out.append(f"{p.position}{pos_rank} by value among available players (value {rec.value:.0f}).")

    if ctx.following_pick is not None:
        gone = 1.0 - rec.survival
        out.append(f"{gone:.0%} chance he is gone before your next pick (#{ctx.following_pick}).")
        if rec.alternative_name:
            out.append(f"If he is gone, most likely fallback at {p.position} is {rec.alternative_name} "
                       f"({rec.alternative_probability:.0%}); expected fallback value "
                       f"{rec.expected_alternative_value:.0f} vs {rec.value:.0f} now.")
        if rec.wait_cost >= 1.0:
            out.append(f"Expected cost of waiting: {rec.wait_cost:.1f} pts.")
        elif rec.survival >= 0.8:
            out.append("Little urgency: he is likely to still be there at your next pick.")

    # Tier cliff: last player of his tier among available at position
    same_tier_left = [g for g in group if g.tier == p.tier]
    later = [g for g in group if g.tier > p.tier]
    if len(same_tier_left) == 1 and later:
        drop = p.projected_points - max(g.projected_points for g in later)
        out.append(f"Last Tier {p.tier} {p.position} on the board; next tier is {drop:.0f} projected points lower.")
    elif len(same_tier_left) > 3:
        out.append(f"Deep tier: {len(same_tier_left)} Tier {p.tier} {p.position}s still available.")

    adp = effective_adp(p, ctx.total_picks)
    if p.adp is not None and adp + 4 < ctx.my_pick:
        out.append(f"Value falling: Yahoo ADP {adp:.0f} vs your pick #{ctx.my_pick}.")

    if not ctx.on_the_clock and rec.availability < 0.999:
        out.append(f"{rec.availability:.0%} chance he reaches your pick #{ctx.my_pick}.")

    if need.multiplier == 0:
        out.append(f"Blocked by roster rules: {need.reason}.")
    elif need.fills_starter:
        out.append(f"Fills an open {p.position} starter slot (need ×{need.multiplier:.2f}).")
    elif need.fills_flex:
        out.append("Would fill the open FLEX slot.")
    else:
        out.append(f"Roster need: {need.reason} (×{need.multiplier:.2f}).")

    if p.risk_label == "BOOM-BUST":
        out.append(f"Experts disagree sharply (rank σ {p.rank_stddev:.1f}, best {p.rank_best:.0f} / "
                   f"worst {p.rank_worst:.0f}): boom-or-bust profile."
                   if p.rank_best is not None else
                   f"Experts disagree sharply (rank σ {p.rank_stddev:.1f}): boom-or-bust profile.")
    elif p.risk_label == "SAFE":
        out.append(f"Experts broadly agree (rank σ {p.rank_stddev:.1f}): safe bet.")
    return out


def snapshot(state: DraftState, recs: list, top_n: int = 10) -> dict:
    """Serialisable snapshot of a user pick for post-draft review (spec §56)."""
    return {
        "pick": state.current_pick,
        "round": state.current_round,
        "roster": state.user_roster_ids(),
        "recommendations": [
            {
                "player": r.player.name, "position": r.player.position, "team": r.player.team,
                "score": r.score, "adjusted_score": r.adjusted_score, "value": r.value, "vor": r.vor,
                "survival": round(r.survival, 3), "availability": round(r.availability, 3),
                "wait_cost": r.wait_cost, "expected_alternative": r.expected_alternative_value,
                "alternative": r.alternative_name, "roster_need": r.roster_need,
                "confidence": r.confidence, "action": r.action, "reasons": r.reasons,
                "risk": r.player.risk_label,
            }
            for r in recs[:top_n]
        ],
    }
