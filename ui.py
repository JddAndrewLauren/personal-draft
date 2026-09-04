"""Presentation layer for app.py: theme CSS, small HTML fragments and pure row builders.

Nothing in here touches session state or the optimizer; every function that takes player
data escapes it before it reaches the page.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

import streamlit as st

ACCENT = "#22d3a5"
POS_COLORS = {"QB": "#f0647a", "RB": "#4ade80", "WR": "#60a5fa", "TE": "#fbbf24", "K": "#c084fc", "DEF": "#94a3b8"}
RISK_COLORS = {"SAFE": "#4ade80", "BALANCED": "#fbbf24", "BOOM-BUST": "#f0647a"}
ACTION_KIND = {
    "TAKE NOW": "accent", "NOW OR NEVER": "accent", "CLOSE DECISION": "amber",
    "SAFE TO WAIT": "amber",
    "LIKELY AVAILABLE LATER": "muted", "ALTERNATIVE": "muted", "LAST PICK": "gray",
}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');
:root { --do-accent: #22d3a5; --do-accent-dim: rgba(34,211,165,.14); --do-amber: #fbbf24; --do-red: #f0647a;
        --do-muted: #8b93a7; --do-line: #262d3b; --do-surface: #161b26; --do-surface-2: #1c2230;
        --do-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }
.block-container { padding: 3.4rem 1.5rem 3rem; max-width: 1600px; }
.stAppDeployButton, [data-testid="stDecoration"] { display: none; }
h1, h2, h3 { letter-spacing: -0.01em; }
[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { border-color: var(--do-line); }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-weight: 600; font-size: .95rem; }
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--do-surface); }
[data-testid="stMetricLabel"] p { color: var(--do-muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
[data-testid="stMetricValue"] { font-family: var(--do-mono); font-size: 1.6rem; }
[data-testid="stDataFrame"] { border-radius: .6rem; overflow: hidden; }
[data-testid="stExpander"] summary { font-weight: 600; }
.stButton button, .stFormSubmitButton button, .stPopover button { font-weight: 600; }
.stButton button[kind="primary"] { box-shadow: 0 0 0 0 var(--do-accent); }
button[data-testid="stBaseButton-primary"] { color: #06231b !important; }
.st-key-reset_draft button { border-color: rgba(240,100,122,.6); color: #fecdd3; }
.st-key-reset_draft button:hover { border-color: var(--do-red); background: rgba(240,100,122,.12); }

/* --- app fragments ------------------------------------------------------- */
.do-wordmark { display: flex; align-items: baseline; gap: .6rem; }
.do-wordmark .name { font-weight: 800; font-size: 1.35rem; letter-spacing: -0.02em; }
.do-wordmark .name b { color: var(--do-accent); }
.do-wordmark .sub { color: var(--do-muted); font-size: .85rem; }
.do-pillrow { display: flex; justify-content: flex-end; align-items: center; gap: .5rem; min-height: 2.2rem; flex-wrap: wrap; }
.do-pill { display: inline-flex; align-items: center; gap: .45rem; padding: .3rem .7rem; border-radius: 999px;
           font-size: .8rem; font-weight: 600; border: 1px solid var(--do-line); background: var(--do-surface); color: #cfd4e0; white-space: nowrap; }
.do-pill .dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--do-muted); }
.do-pill.ok .dot { background: var(--do-accent); box-shadow: 0 0 0 3px var(--do-accent-dim); }
.do-pill.warn { border-color: rgba(251,191,36,.5); color: #fde68a; } .do-pill.warn .dot { background: var(--do-amber); }
.do-pill.bad { border-color: rgba(240,100,122,.6); color: #fecdd3; background: rgba(240,100,122,.12); } .do-pill.bad .dot { background: var(--do-red); }
.do-pill .age { color: var(--do-muted); font-weight: 500; }

.do-clock { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: .35rem 0 .9rem;
            padding: .8rem 1.1rem; border-radius: .7rem; background: linear-gradient(90deg, var(--do-accent), #16a37f);
            color: #06231b; font-weight: 800; letter-spacing: .04em; animation: do-pulse 2.2s ease-in-out infinite; }
.do-clock .big { font-size: 1.15rem; } .do-clock .pick { font-family: var(--do-mono); font-size: 1rem; font-weight: 600; opacity: .85; }
@keyframes do-pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(34,211,165,.0); } 50% { box-shadow: 0 0 0 6px rgba(34,211,165,.22); } }

.do-title { display: flex; align-items: center; justify-content: space-between; gap: .6rem; margin-bottom: .55rem; }
.do-title .t { font-weight: 700; font-size: .95rem; } .do-title .s { color: var(--do-muted); font-size: .8rem; }

.do-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: .5rem; }
.do-stats.two { grid-template-columns: repeat(2, 1fr); }
.do-stat { background: var(--do-surface-2); border: 1px solid var(--do-line); border-radius: .55rem; padding: .55rem .7rem; min-width: 0; }
.do-stat .l { color: var(--do-muted); font-size: .7rem; text-transform: uppercase; letter-spacing: .05em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.do-stat .v { font-family: var(--do-mono); font-size: 1.35rem; font-weight: 600; line-height: 1.25; margin-top: .1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.do-stat .v.text { font-family: inherit; font-size: 1rem; font-weight: 600; }
.do-stat .s { color: var(--do-muted); font-size: .72rem; margin-top: .1rem; }
.do-stat.hi { border-color: rgba(34,211,165,.45); background: var(--do-accent-dim); }

.do-chip { display: inline-block; padding: .1rem .45rem; border-radius: .35rem; font-size: .72rem; font-weight: 700; letter-spacing: .04em;
           color: #0b0e14; line-height: 1.35; font-family: var(--do-mono); }
.do-chip.ghost { background: transparent !important; border: 1px dashed var(--do-line); color: var(--do-muted); }
.do-run { display: flex; gap: .3rem; flex-wrap: wrap; align-items: center; margin-top: .6rem; }
.do-run .lbl { color: var(--do-muted); font-size: .75rem; margin-right: .2rem; }

.do-badge { display: inline-block; padding: .18rem .55rem; border-radius: .4rem; font-size: .72rem; font-weight: 700; letter-spacing: .05em; white-space: nowrap; }
.do-badge.accent { background: var(--do-accent); color: #06231b; }
.do-badge.amber { background: rgba(251,191,36,.18); color: #fde68a; border: 1px solid rgba(251,191,36,.45); }
.do-badge.muted { background: transparent; color: #cfd4e0; border: 1px solid var(--do-line); }
.do-badge.gray { background: var(--do-surface-2); color: var(--do-muted); }
.do-badge.red { background: rgba(240,100,122,.16); color: #fecdd3; border: 1px solid rgba(240,100,122,.5); }
.do-risk { display: inline-flex; align-items: center; gap: .35rem; font-size: .75rem; color: var(--do-muted); font-weight: 600; white-space: nowrap; }
.do-risk i { width: .55rem; height: .55rem; border-radius: 50%; display: inline-block; }

.do-roster { display: grid; grid-template-columns: auto 1fr auto; gap: .28rem .6rem; align-items: center; font-size: .88rem; }
.do-roster .n { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.do-roster .n.empty { color: var(--do-muted); }
.do-roster .p { font-family: var(--do-mono); color: var(--do-muted); font-size: .8rem; text-align: right; }

.do-hero { display: flex; flex-direction: column; gap: .5rem; }
.do-hero .head { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.do-hero .rank { font-family: var(--do-mono); color: var(--do-muted); font-size: 1rem; }
.do-hero .name { font-size: 1.45rem; font-weight: 800; letter-spacing: -0.02em; line-height: 1.15; }
.do-hero .team { color: var(--do-muted); font-weight: 500; }
.do-hero .meta { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.do-hero .detail { color: var(--do-muted); font-size: .82rem; }
.do-reasons { margin: .15rem 0 0; padding-left: 1.1rem; font-size: .86rem; color: #cfd4e0; }
.do-reasons li { margin: .12rem 0; }

.do-row { display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.do-row .top { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.do-row .rank { font-family: var(--do-mono); color: var(--do-muted); font-size: .85rem; }
.do-row .name { font-weight: 700; font-size: 1rem; }
.do-row .team { color: var(--do-muted); font-size: .85rem; }
.do-kv { display: flex; gap: .9rem; flex-wrap: wrap; font-size: .8rem; color: var(--do-muted); }
.do-kv b { font-family: var(--do-mono); color: #e6e8ee; font-weight: 600; }

.do-empty { text-align: center; padding: 3rem 1rem; border: 1px dashed var(--do-line); border-radius: .8rem; color: var(--do-muted); }
.do-empty .h { font-size: 1.2rem; font-weight: 700; color: #e6e8ee; margin-bottom: .4rem; }
.do-empty ol { display: inline-block; text-align: left; margin: .6rem 0 0; padding-left: 1.2rem; }

.do-checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .55rem; }
.do-check { display: flex; gap: .65rem; align-items: flex-start; padding: .65rem .8rem; border: 1px solid var(--do-line); border-radius: .6rem; background: var(--do-surface); }
.do-check i { flex: none; width: .65rem; height: .65rem; border-radius: 50%; margin-top: .35rem; background: var(--do-red); }
.do-check.ok i { background: var(--do-accent); box-shadow: 0 0 0 3px var(--do-accent-dim); }
.do-check .l { font-weight: 600; font-size: .9rem; } .do-check .n { color: var(--do-muted); font-size: .8rem; }

.do-section { display: flex; align-items: center; gap: .6rem; margin: .4rem 0 .3rem; }
.do-section .t { font-size: 1.05rem; font-weight: 700; }

/* --- split-window (about 750px) behaviour --------------------------------- */
@media (max-width: 1000px) {
  .block-container { padding: 3.2rem .9rem 2.5rem; }
  .st-key-topbar > * > [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
  .st-key-topbar > * > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }
  .st-key-topbar > * > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; }
  .st-key-hero > * > [data-testid="stHorizontalBlock"],
  .st-key-filters > * > [data-testid="stHorizontalBlock"],
  .st-key-manual [data-testid="stForm"] [data-testid="stHorizontalBlock"],
  .st-key-recs [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
  .st-key-hero > * > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-filters > * > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; }
  .st-key-manual [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 46% !important; width: 46% !important; min-width: 46% !important; }
  .st-key-recs [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }
  .st-key-recs [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex: 1 1 100% !important; width: 100% !important; }
  .do-pillrow { justify-content: flex-start; }
  .do-stats { grid-template-columns: repeat(auto-fit, minmax(95px, 1fr)); }
  .hide-narrow { display: none !important; }
}
"""


def inject_css() -> None:
    st.html(f"<style>{CSS}</style>")


# --------------------------------------------------------------------------- #
# HTML fragments (all text escaped)
# --------------------------------------------------------------------------- #


def pos_chip(pos: str, ghost: bool = False) -> str:
    color = POS_COLORS.get(pos.rstrip("+"), "#94a3b8")
    cls = "do-chip ghost" if ghost else "do-chip"
    return f'<span class="{cls}" style="background:{color}">{escape(pos)}</span>'


def badge(text: str, kind: str = "muted") -> str:
    return f'<span class="do-badge {escape(kind)}">{escape(text)}</span>'


def action_badge(action: str) -> str:
    return badge(action, ACTION_KIND.get(action, "muted")) if action else ""


def risk_dot(label: str) -> str:
    color = RISK_COLORS.get(label)
    if not color:
        return ""
    return f'<span class="do-risk"><i style="background:{color}"></i>{escape(label)}</span>'


def pill(text: str, kind: str = "muted", age: str | None = None) -> str:
    age_html = f'<span class="age">{escape(age)}</span>' if age else ""
    return f'<span class="do-pill {escape(kind)}"><span class="dot"></span>{escape(text)}{age_html}</span>'


def wordmark(sub: str) -> str:
    return (f'<div class="do-wordmark"><span class="name">Draft<b>Optimizer</b></span>'
            f'<span class="sub">{escape(sub)}</span></div>')


def clock_banner(pick: int, round_no: int) -> str:
    return (f'<div class="do-clock"><span class="big">YOU ARE ON THE CLOCK</span>'
            f'<span class="pick">pick {pick} · round {round_no}</span></div>')


def card_title(title: str, sub: str = "", right: str = "") -> str:
    sub_html = f'<span class="s">{escape(sub)}</span>' if sub else ""
    return f'<div class="do-title"><span><span class="t">{escape(title)}</span> {sub_html}</span>{right}</div>'


def section(title: str, right: str = "") -> str:
    return f'<div class="do-section"><span class="t">{escape(title)}</span>{right}</div>'


def stat_grid(stats: list[tuple[str, str, str]], two: bool = False, highlight: int | None = None) -> str:
    """stats: (label, value, sub). Text values (team names) get a smaller non-mono face."""
    cells = []
    for i, (label, value, sub) in enumerate(stats):
        vcls = "v" if value.replace(".", "").replace("%", "").replace("—", "").replace("-", "").isdigit() or value == "—" else "v text"
        cls = "do-stat hi" if highlight == i else "do-stat"
        sub_html = f'<div class="s">{escape(sub)}</div>' if sub else ""
        cells.append(f'<div class="{cls}"><div class="l">{escape(label)}</div><div class="{vcls}">{escape(value)}</div>{sub_html}</div>')
    return f'<div class="do-stats{" two" if two else ""}">{"".join(cells)}</div>'


def pick_run(positions: list[str], label: str = "Last picks") -> str:
    chips = "".join(pos_chip(p) for p in positions)
    return f'<div class="do-run"><span class="lbl">{escape(label)}</span>{chips}</div>'


def kv(pairs: list[tuple[str, str]]) -> str:
    return '<div class="do-kv">' + "".join(f"<span>{escape(k)} <b>{escape(v)}</b></span>" for k, v in pairs) + "</div>"


def reasons_list(reasons: list[str]) -> str:
    if not reasons:
        return ""
    return '<ul class="do-reasons">' + "".join(f"<li>{escape(r)}</li>" for r in reasons) + "</ul>"


def rec_hero_html(rank: int, name: str, pos: str, team: str, action: str, risk: str,
                  stats: list[tuple[str, str, str]], detail: str, reasons: list[str]) -> str:
    return (
        '<div class="do-hero">'
        f'<div class="head"><span class="rank">#{rank}</span><span class="name">{escape(name)}</span>'
        f'{pos_chip(pos)}<span class="team">{escape(team)}</span></div>'
        f'<div class="meta">{action_badge(action)}{risk_dot(risk)}</div>'
        f'{stat_grid(stats, highlight=0)}'
        f'<div class="detail">{escape(detail)}</div>{reasons_list(reasons)}</div>'
    )


def rec_row_html(rank: int, name: str, pos: str, team: str, action: str, risk: str) -> str:
    return (
        '<div class="do-row">'
        f'<div class="top"><span class="rank">#{rank}</span><span class="name">{escape(name)}</span>'
        f'{pos_chip(pos)}<span class="team hide-narrow">{escape(team)}</span></div>'
        f'<div class="top">{action_badge(action)}{risk_dot(risk)}</div></div>'
    )


def empty_state() -> str:
    return ('<div class="do-empty"><div class="h">No player data loaded</div>'
            "Open the sidebar and load a projections CSV to start the board."
            "<ol><li>Sidebar → <b>Data</b> → upload <i>Players CSV</i> (or place it at data/players.csv and Reload).</li>"
            "<li>Set Teams, Rounds and your draft slot under <b>League</b>.</li>"
            "<li>Pick a live source under <b>Pick source</b>, or mark picks by hand.</li></ol></div>")


# --------------------------------------------------------------------------- #
# Pure builders (unit-tested)
# --------------------------------------------------------------------------- #


@dataclass
class RosterRow:
    slot: str            # "RB", "RB+" (overflow), "?" (unmapped)
    name: str | None     # None -> empty slot
    proj: float | None


def roster_rows(roster_slots: dict, assigned: dict, unknown_names: list[str]) -> list[RosterRow]:
    """One row per configured slot, then overflow rows, then unmapped picks.

    roster_slots: {pos: count} in display order; assigned: {pos: [Player]} from assign_roster_slots.
    """
    rows: list[RosterRow] = []
    for pos, n in roster_slots.items():
        got = assigned.get(pos, [])
        for i in range(n):
            p = got[i] if i < len(got) else None
            rows.append(RosterRow(pos, p.name if p else None, p.projected_points if p else None))
        for extra in got[n:]:
            rows.append(RosterRow(f"{pos}+", extra.name, extra.projected_points))
    for name in unknown_names:
        rows.append(RosterRow("?", name, None))
    return rows


def roster_grid(rows: list[RosterRow]) -> str:
    cells = []
    for r in rows:
        if r.name is None:
            cells.append(f'{pos_chip(r.slot, ghost=True)}<span class="n empty">—</span><span class="p"></span>')
        else:
            proj = f"{r.proj:.0f}" if r.proj is not None else ""
            cells.append(f'{pos_chip(r.slot)}<span class="n">{escape(r.name)}</span><span class="p">{proj}</span>')
    return '<div class="do-roster">' + "".join(cells) + "</div>"


CORE_COLUMNS = ["Rank", "Player", "Pos", "Team", "Proj", "Value", "Tier", "Yahoo ADP",
                "Reach my pick", "Survival", "Wait cost", "Score", "Risk"]
EXTRA_COLUMNS = ["VOR", "Ext VBD", "Need", "Rank σ"]


def available_rows(recs, flex_positions, on_the_clock: bool, following_pick, pos: str, risk: str, query: str) -> list[dict]:
    """Filter recommendations into table rows. Mirrors the draft-page filter semantics."""
    q = query.strip().lower()
    rows = []
    for rank, r in enumerate(recs, start=1):
        p = r.player
        if pos == "FLEX" and p.position not in flex_positions:
            continue
        if pos not in ("ALL", "FLEX") and p.position != pos:
            continue
        if risk != "ALL" and p.risk_label != risk:
            continue
        if q and q not in p.name.lower():
            continue
        rows.append({
            "Rank": rank, "Player": p.name, "Pos": p.position, "Team": p.team,
            "Proj": p.projected_points, "VOR": p.vor,
            "Ext VBD": p.external_vbd_scaled, "Value": p.value, "Tier": p.tier,
            "Yahoo ADP": p.adp, "Reach my pick": None if on_the_clock else r.availability,
            "Survival": r.survival if following_pick else None, "Wait cost": r.wait_cost,
            "Need": r.roster_need, "Score": r.adjusted_score,
            "Risk": p.risk_label, "Rank σ": p.rank_stddev,
        })
    return rows


def table_columns(on_the_clock: bool, all_columns: bool) -> list[str]:
    cols = CORE_COLUMNS + (EXTRA_COLUMNS if all_columns else [])
    return [c for c in cols if not (on_the_clock and c == "Reach my pick")]


def check_cards(checks: list[tuple[str, bool, str]]) -> str:
    cards = "".join(
        f'<div class="do-check{" ok" if ok else ""}"><i></i><div><div class="l">{escape(label)}</div>'
        f'<div class="n">{escape(note)}</div></div></div>'
        for label, ok, note in checks
    )
    return f'<div class="do-checks">{cards}</div>'
