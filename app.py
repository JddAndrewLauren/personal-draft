"""Streamlit UI for the Yahoo fantasy football draft optimizer.

    streamlit run app.py

Everything Yahoo-related is optional: the draft board, manual picks and recommendations
work from data/players.csv alone.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import scrape as sc
import yahoo as yh
from models import (
    DraftState,
    Player,
    apply_mappings,
    default_teams,
    detect_format,
    load_config,
    load_mappings,
    load_players,
    merge_external,
    players_by_id,
    read_external,
    reconcile_rounds,
    save_mappings,
    settings_from_config,
    write_players_csv,
)
from optimizer import (
    assign_roster_slots,
    merge_config,
    prepare_players,
    recommend,
    snapshot,
)

st.set_page_config(page_title="Draft Optimizer", page_icon="🏈", layout="wide")
load_dotenv()

RISK_ICON = {"SAFE": "🟢 SAFE", "BALANCED": "🟡 BALANCED", "BOOM-BUST": "🔴 BOOM-BUST", "—": "—"}

# --------------------------------------------------------------------------- #
# Session bootstrap
# --------------------------------------------------------------------------- #


def setup_logging(path: str) -> logging.Logger:
    logger = logging.getLogger("fantasy-draft")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(fh)
    return logger


def ss():
    return st.session_state


def init():
    if ss().get("_ready"):
        return
    cfg = load_config("config.yaml")
    ss().cfg = cfg
    ss().paths = cfg["paths"]
    ss().log = setup_logging(cfg["paths"]["log_file"])
    ss().ocfg = merge_config(cfg.get("optimizer"))
    ss().poll_interval = int((cfg.get("polling") or {}).get("interval_seconds", 5))
    ss().poll_enabled = False
    ss().page = "Draft"
    ss().players = []
    ss().messages = []
    ss().yahoo = None
    ss().scrape_enabled = False
    ss().scrape_path = cfg["paths"].get("scrape_picks", "scrape/picks.json")
    ss().scrape_team = ""
    ss().scrape_teams = []
    ss().scrape_updated = 0.0
    ss().yahoo_league_key = os.environ.get("YAHOO_LEAGUE_KEY") or None
    ss().yahoo_leagues = []
    ss().yahoo_players = yh.load_yahoo_players(cfg["paths"]["yahoo_players_csv"])
    ss().yahoo_ready = {}
    ss().mapping = {}
    ss().unmatched = []
    ss().external_reports = []
    ss().snapshots_done = set()

    state_path = Path(cfg["paths"]["draft_state"])
    if state_path.exists():
        try:
            ss().state = DraftState.load(state_path)
            ss().messages.append(f"Restored draft state from {state_path} ({len(ss().state.picks)} picks).")
        except Exception as exc:  # noqa: BLE001
            ss().messages.append(f"Could not load {state_path}: {exc}; starting fresh.")
            ss().state = new_state(cfg)
    else:
        ss().state = new_state(cfg)

    try:
        load_player_file(cfg["paths"]["players_csv"])
    except Exception as exc:  # noqa: BLE001
        ss().messages.append(f"No player data loaded: {exc}")
    ss()._ready = True


def new_state(cfg) -> DraftState:
    settings = settings_from_config(cfg)
    user_slot = int((cfg.get("draft") or {}).get("user_slot", 1))
    return DraftState(settings=settings, teams=default_teams(settings.num_teams, user_slot), user_slot=user_slot)


def load_player_file(source, label: str = "players.csv"):
    scoring = ss().state.settings.scoring or None
    players = load_players(source, scoring=scoring)
    if not players:
        raise ValueError("no rows")
    mappings = load_mappings(ss().paths["mappings_csv"])
    apply_mappings(players, mappings)
    ss().players = players
    ss().players_source = label
    reprepare()
    ss().log.info("Loaded %d players from %s", len(players), label)


def reprepare():
    """Recompute VOR / tiers / blend / risk after settings or data changes."""
    if ss().players:
        ss().replacement = prepare_players(ss().players, ss().state.settings, ss().ocfg)
        ss().by_id = players_by_id(ss().players)
        refresh_mapping()
    else:
        ss().replacement, ss().by_id = {}, {}


def refresh_mapping():
    if ss().players and ss().yahoo_players:
        manual = load_mappings(ss().paths["mappings_csv"])
        res = yh.build_mappings(ss().players, ss().yahoo_players, manual)
        ss().mapping = res["mapping"]
        ss().unmatched = res["unmatched"]
        for p in ss().players:
            if p.player_id in ss().mapping:
                p.yahoo_player_id = ss().mapping[p.player_id]


def save_state():
    try:
        ss().state.save(ss().paths["draft_state"])
    except Exception as exc:  # noqa: BLE001
        ss().log.error("Could not save draft state: %s", exc)


def player_label(p: Player) -> str:
    adp = f"ADP {p.adp:.0f}" if p.adp is not None else "no ADP"
    return f"{p.name} ({p.position} {p.team}) · {adp}"


def name_of(pid: str, fallback: str | None = None) -> str:
    p = ss().by_id.get(pid) if ss().get("by_id") else None
    if p:
        return p.name
    return fallback or pid


# --------------------------------------------------------------------------- #
# Draft actions
# --------------------------------------------------------------------------- #


def do_manual_pick(player: Player, slot: int | None, pick: int | None):
    state: DraftState = ss().state
    try:
        dp = state.add_pick(player.player_id, slot=slot, pick=pick, player_name=player.name, source="manual")
    except ValueError as exc:
        st.error(str(exc))
        return
    save_state()
    ss().log.info("Manual pick %d: %s -> %s", dp.pick, player.name, state.team_name(dp.slot))
    st.toast(f"Pick {dp.pick}: {player.name} → {state.team_name(dp.slot)}")


def do_undo():
    last = ss().state.undo_last()
    if last:
        save_state()
        ss().log.info("Undo pick %d (%s)", last.pick, last.player_name or last.player_id)
        st.toast(f"Undid pick {last.pick}: {name_of(last.player_id, last.player_name)}")


def yahoo_sync(manual: bool = False) -> dict:
    """Pull Yahoo draft results and merge them into local state. Never raises."""
    state: DraftState = ss().state
    client: yh.YahooClient | None = ss().yahoo
    key = ss().yahoo_league_key
    result = {"new": 0, "conflicts": 0, "error": None}
    if client is None or not key:
        return result
    try:
        results = yh.fetch_draft_results(client, key)
        slots = yh.assign_draft_slots(state.teams, results, state.num_teams)
        me = next((t for t in state.teams if t.is_user), None)
        if slots and me and me.team_key in slots and me.slot != state.user_slot:
            ss().log.info("Draft order from Yahoo: your slot is %d", me.slot)
            state.user_slot = me.slot
        yahoo_to_local = {v: k for k, v in ss().mapping.items()}
        names = {r["yahoo_player_id"]: r["name"] for r in ss().yahoo_players}
        picks = yh.draft_picks_from_results(results, state.teams, yahoo_to_local, state.num_teams, names)
        new, conflicts = state.merge_yahoo(picks)
        state.last_sync = time.time()
        state.sync_status = "connected"
        state.sync_message = f"{len(results)} picks reported by Yahoo"
        result["new"], result["conflicts"] = len(new), len(conflicts)
        if new or conflicts:
            save_state()
            for p in new:
                ss().log.info("Yahoo pick %d: %s -> %s", p.pick, p.player_name or p.player_id, state.team_name(p.slot))
            for c in conflicts:
                ss().log.warning("Sync conflict at pick %d: local %s vs yahoo %s", c.pick,
                                 c.local_player_name or c.local_player_id, c.yahoo_player_name or c.yahoo_player_id)
        elif manual:
            save_state()
    except Exception as exc:  # noqa: BLE001
        state.sync_status = "lost"
        state.sync_message = str(exc)[:200]
        result["error"] = str(exc)
        ss().log.error("Yahoo sync failed: %s", exc)
    return result


def scrape_sync(manual: bool = False) -> dict:
    """Merge picks from the draft-room scrape feed (scrape/picks.json). Never raises."""
    state: DraftState = ss().state
    result = {"new": 0, "conflicts": 0, "error": None}
    try:
        feed = sc.load_picks(ss().scrape_path)
        rows = feed["picks"]
        if not manual and feed["updated"] and feed["updated"] == ss().scrape_updated:
            return result
        ss().scrape_updated = feed["updated"]
        ss().scrape_teams = sorted({r["team"] for r in rows if r["team"]})
        slots = sc.assign_slots_from_names(rows, state.num_teams)
        me = ss().scrape_team
        if me and me in slots and slots[me] != state.user_slot:
            ss().log.info("Draft order from scrape: your slot is %d", slots[me])
            state.user_slot = slots[me]
            for t in state.teams:
                t.is_user = t.slot == slots[me]
        for name, slot in slots.items():
            t = next((t for t in state.teams if t.slot == slot), None)
            if t is not None and t.name.startswith("Team "):
                t.name = name
        picks = sc.draft_picks_from_scrape(rows, ss().players, state.num_teams, slots)
        new, conflicts = state.merge_yahoo(picks, source="scrape")
        state.last_sync = time.time()
        state.sync_status = "connected"
        state.sync_message = f"{len(rows)} picks in draft-room feed"
        result["new"], result["conflicts"] = len(new), len(conflicts)
        if new or conflicts:
            save_state()
            for p in new:
                ss().log.info("Scrape pick %d: %s -> %s", p.pick, p.player_name or p.player_id, state.team_name(p.slot))
            for c in conflicts:
                ss().log.warning("Sync conflict at pick %d: local %s vs scrape %s", c.pick,
                                 c.local_player_name or c.local_player_id, c.yahoo_player_name or c.yahoo_player_id)
        elif manual:
            save_state()
    except Exception as exc:  # noqa: BLE001
        state.sync_status = "lost"
        state.sync_message = str(exc)[:200]
        result["error"] = str(exc)
        ss().log.error("Scrape sync failed: %s", exc)
    return result


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #


def sidebar():
    state: DraftState = ss().state
    with st.sidebar:
        ss().page = st.radio("Page", ["Draft", "Readiness"], horizontal=True, index=0 if ss().page == "Draft" else 1)

        st.subheader("Data")
        up = st.file_uploader("Players CSV (projections + Yahoo ADP)", type=["csv"], key="players_upload")
        if up is not None and ss().get("_last_upload") != up.name + str(up.size):
            try:
                load_player_file(io.StringIO(up.getvalue().decode("utf-8-sig")), label=up.name)
                ss()._last_upload = up.name + str(up.size)
                st.success(f"Loaded {len(ss().players)} players from {up.name}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load CSV: {exc}")
        ext = st.file_uploader("External VBD / rankings CSV (FantasyPros or generic)", type=["csv"], key="ext_upload")
        fmt_choice = st.selectbox("External format", ["auto-detect", "FantasyPros ECR / rankings",
                                                       "FantasyPros projections", "generic (config column_map)"])
        ext_pos = st.selectbox("Position (single-position exports only)", ["auto", "QB", "RB", "WR", "TE", "K", "DEF"])
        if ext is not None and st.button("Import external data", use_container_width=True):
            import_external(ext, fmt_choice, ext_pos)
        c1, c2 = st.columns(2)
        if c1.button("Reload from disk", use_container_width=True):
            try:
                load_player_file(ss().paths["players_csv"])
                st.success("Reloaded")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if c2.button("Save enriched CSV", use_container_width=True, help="Writes data/players.csv with imported columns"):
            write_players_csv(ss().players, ss().paths["players_csv"])
            st.success("Saved data/players.csv")
        if ss().players:
            st.caption(f"{len(ss().players)} players · source: {ss().get('players_source', '')}")

        st.subheader("League")
        yahoo_locked = bool(state.settings.league_key)
        teams_n = st.number_input("Teams", 2, 20, state.settings.num_teams, disabled=yahoo_locked)
        rounds_n = st.number_input("Rounds", 1, 30, state.settings.rounds, disabled=yahoo_locked)
        slot_n = st.number_input("Your draft slot", 1, int(teams_n), min(state.user_slot, int(teams_n)))
        if (teams_n, rounds_n, slot_n) != (state.settings.num_teams, state.settings.rounds, state.user_slot):
            if state.picks and teams_n != state.settings.num_teams:
                st.warning("Changing team count with picks recorded; pick→slot mapping is recomputed.")
            state.settings.num_teams = int(teams_n)
            state.settings.rounds = int(rounds_n)
            reconcile_rounds(state.settings.roster, int(rounds_n))
            state.user_slot = int(slot_n)
            if not yahoo_locked:
                state.teams = default_teams(int(teams_n), int(slot_n))
            reprepare()
            save_state()
        roster_txt = ", ".join(f"{k}{v}" for k, v in state.settings.roster.slots.items())
        st.caption(f"Roster: {roster_txt}" + (" (from Yahoo)" if yahoo_locked else " (config.yaml)"))

        st.subheader("Optimizer")
        o = ss().ocfg
        lam = st.slider("Wait-cost weight λ", 0.0, 3.0, float(o["wait_cost_weight"]), 0.1)
        sd = st.slider("ADP σ (base picks)", 1.0, 20.0, float(o["adp_stddev"]), 0.5)
        w = st.slider("External VBD weight", 0.0, 1.0, float(o["external_vbd_weight"]), 0.05)
        topn = st.slider("Recommendations shown", 3, 8, int(o["top_n"]))
        if (lam, sd, w, topn) != (o["wait_cost_weight"], o["adp_stddev"], o["external_vbd_weight"], o["top_n"]):
            o["wait_cost_weight"], o["adp_stddev"], o["external_vbd_weight"], o["top_n"] = lam, sd, w, topn
            reprepare()

        st.subheader("Yahoo")
        yahoo_sidebar()

        st.subheader("Draft room (Claude in Chrome)")
        scrape_sidebar()

        st.subheader("State")
        c1, c2 = st.columns(2)
        if c1.button("Save now", use_container_width=True):
            save_state()
            st.success("Saved")
        if c2.button("Reload saved", use_container_width=True):
            p = Path(ss().paths["draft_state"])
            if p.exists():
                ss().state = DraftState.load(p)
                reprepare()
                st.success("Reloaded")
        if st.checkbox("Enable reset"):
            if st.button("Reset draft (clear all picks)", type="primary", use_container_width=True):
                state.reset()
                save_state()
                ss().snapshots_done = set()
                ss().log.info("Draft reset")
                st.rerun()


def import_external(upload, fmt_choice: str, ext_pos: str):
    fmt = {"auto-detect": None, "FantasyPros ECR / rankings": "fantasypros_ecr",
           "FantasyPros projections": "fantasypros_projections", "generic (config column_map)": "generic"}[fmt_choice]
    try:
        text = upload.getvalue().decode("utf-8-sig")
        rows = read_external(io.StringIO(text), fmt=fmt,
                             column_map=(ss().cfg.get("import") or {}).get("column_map"),
                             position=None if ext_pos == "auto" else ext_pos,
                             scoring=ss().state.settings.scoring or None)
        res = merge_external(ss().players, rows, overwrite_points=(fmt == "fantasypros_projections"))
        reprepare()
        detected = fmt or detect_format(text.splitlines()[0].split(","))
        ss().external_reports.append({"file": upload.name, "format": detected, "rows": len(rows),
                                      "matched": res["matched"], "unmatched": [r["name"] for r in res["unmatched"]]})
        ss().log.info("Imported %s (%s): %d rows, %d matched", upload.name, detected, len(rows), res["matched"])
        st.success(f"{upload.name}: {res['matched']} of {len(rows)} rows matched ({detected})")
        if res["unmatched"]:
            st.warning(f"Unmatched: {', '.join(r['name'] for r in res['unmatched'][:8])}"
                       + (" …" if len(res["unmatched"]) > 8 else ""))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Import failed: {exc}")


def yahoo_sidebar():
    state: DraftState = ss().state
    cid, secret = os.environ.get("YAHOO_CLIENT_ID", ""), os.environ.get("YAHOO_CLIENT_SECRET", "")
    if not cid or not secret:
        st.caption("Set YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET in .env to enable Yahoo sync. Manual mode works without it.")
        return
    if ss().yahoo is None:
        try:
            ss().yahoo = yh.YahooClient(cid, secret, token_path=ss().paths["token_file"])
        except yh.YahooAuthError as exc:
            st.error(str(exc))
            return
    client: yh.YahooClient = ss().yahoo
    if not client.has_token:
        st.link_button("1. Authorize with Yahoo", client.authorize_url(), use_container_width=True)
        code = st.text_input("2. Paste the URL Yahoo redirects to (the localhost page will not load; copy it from the address bar)")
        if code and st.button("3. Exchange code", use_container_width=True):
            try:
                client.exchange_code(code)
                ss().log.info("Yahoo authorization complete")
                st.success("Authorized")
                st.rerun()
            except yh.YahooAuthError as exc:
                st.error(str(exc))
        return

    st.caption("Authorized ✓")
    if st.button("Fetch my leagues", use_container_width=True):
        try:
            ss().yahoo_leagues = yh.fetch_leagues(client)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not fetch leagues: {exc}")
    options = [l["league_key"] for l in ss().yahoo_leagues]
    labels = {l["league_key"]: f"{l['name']} ({l['num_teams']} teams, {l['draft_status']})" for l in ss().yahoo_leagues}
    if ss().yahoo_league_key and ss().yahoo_league_key not in options:
        options.insert(0, ss().yahoo_league_key)
    if options:
        idx = options.index(ss().yahoo_league_key) if ss().yahoo_league_key in options else 0
        ss().yahoo_league_key = st.selectbox("League", options, index=idx, format_func=lambda k: labels.get(k, k))
    else:
        ss().yahoo_league_key = st.text_input("League key (e.g. 461.l.12345)", ss().yahoo_league_key or "") or None

    if ss().yahoo_league_key and st.button("Load league settings & teams", use_container_width=True):
        load_yahoo_league(client, ss().yahoo_league_key)

    if state.settings.league_key:
        st.caption(f"League: {state.settings.name} ({state.settings.num_teams} teams)")
        ss().poll_enabled = st.toggle("Live sync (poll draft results)", value=ss().poll_enabled)
        ss().poll_interval = st.number_input("Poll every (s)", 2, 60, ss().poll_interval)
        if st.button("Sync now", use_container_width=True):
            r = yahoo_sync(manual=True)
            if r["error"]:
                st.error(r["error"])
            else:
                st.success(f"Synced: {r['new']} new picks, {r['conflicts']} conflicts")
    if st.button("Forget Yahoo token", use_container_width=True):
        client.clear_token()
        ss().yahoo = None
        st.rerun()


def scrape_sidebar():
    """Feed from the Yahoo draft-room page, written by the /watch-draft loop. No API needed."""
    ss().scrape_enabled = st.toggle("Watch scrape feed", value=ss().scrape_enabled,
                                    help="Run `/loop 30s /watch-draft` in Claude Code with the draft room open.")
    st.caption(f"Feed: `{ss().scrape_path}`")
    opts = list(ss().scrape_teams)
    if ss().scrape_team and ss().scrape_team not in opts:
        opts.insert(0, ss().scrape_team)
    if opts:
        idx = opts.index(ss().scrape_team) if ss().scrape_team in opts else 0
        ss().scrape_team = st.selectbox("My team name (as shown in the draft room)", opts, index=idx)
    else:
        ss().scrape_team = st.text_input("My team name (as shown in the draft room)", ss().scrape_team)
    if ss().scrape_enabled:
        ss().poll_interval = st.number_input("Poll every (s)", 2, 60, ss().poll_interval, key="scrape_poll")
    if st.button("Sync feed now", use_container_width=True):
        r = scrape_sync(manual=True)
        if r["error"]:
            st.error(r["error"])
        else:
            st.success(f"Synced: {r['new']} new picks, {r['conflicts']} conflicts")


def load_yahoo_league(client: yh.YahooClient, key: str):
    state: DraftState = ss().state
    try:
        settings = yh.fetch_settings(client, key)
        teams = yh.fetch_teams(client, key)
        my_keys = set(yh.fetch_user_team_keys(client))
        for t in teams:
            t.is_user = t.is_user or (t.team_key in my_keys)
        state.settings = settings
        state.teams = teams
        # Draft order is only knowable from round-1 picks; until then the config slot stands.
        results = yh.fetch_draft_results(client, key)
        slots = yh.assign_draft_slots(state.teams, results, settings.num_teams)
        me = next((t for t in teams if t.is_user), None)
        if slots and me is not None and me.team_key in slots:
            state.user_slot = me.slot
        else:
            st.info(f"Yahoo has not published the draft order yet; using slot {state.user_slot}. "
                    "Adjust 'Your draft slot' in the sidebar if needed; it auto-corrects once round 1 begins.")
        reprepare()
        save_state()
        ss().log.info("Loaded Yahoo league %s (%s): %d teams, roster %s", key, settings.name, len(teams), settings.roster.slots)
        st.success(f"Loaded {settings.name}: {settings.num_teams} teams, {settings.rounds} rounds")
        if results:
            yahoo_sync(manual=True)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load league: {exc}")
        ss().log.error("Load league failed: %s", exc)


# --------------------------------------------------------------------------- #
# Draft page
# --------------------------------------------------------------------------- #


def draft_page():
    state: DraftState = ss().state
    players = ss().players
    for m in ss().messages:
        st.info(m)
    ss().messages = []
    if not players:
        st.warning("Load a players CSV in the sidebar to begin.")
        return

    yahoo_live = ss().poll_enabled and ss().yahoo is not None and ss().yahoo_league_key
    if yahoo_live or ss().scrape_enabled:
        interval = max(2, int(ss().poll_interval))
        st.fragment(run_every=f"{interval}s")(poll_body)()

    recs = recommend(state, players, ss().ocfg)
    maybe_snapshot(recs)

    col_state, col_roster, col_recs = st.columns([1.0, 1.0, 2.2])
    with col_state:
        draft_state_panel()
    with col_roster:
        roster_panel()
    with col_recs:
        recommendations_panel(recs)

    if state.conflicts:
        conflicts_panel()

    st.divider()
    available_table(recs)
    st.divider()
    manual_controls()
    history_panel()


def poll_body():
    """Body of the auto-refreshing polling fragment (wrapped with st.fragment in draft_page)."""
    yahoo_live = ss().poll_enabled and ss().yahoo is not None and ss().yahoo_league_key
    r = yahoo_sync() if yahoo_live else scrape_sync()
    state: DraftState = ss().state
    if r["new"] or r["conflicts"]:
        st.rerun(scope="app")
    age = f"{time.time() - state.last_sync:.0f}s ago" if state.last_sync else "never"
    label = "Yahoo" if yahoo_live else "Draft-room feed"
    if state.sync_status == "lost":
        st.error(f"{label.upper()} SYNC LOST — manual mode active. Last update: {age}. {state.sync_message}")
        return
    if not yahoo_live:
        feed_age = time.time() - ss().scrape_updated if ss().scrape_updated else None
        if feed_age is None:
            st.warning(f"Draft-room feed not written yet ({ss().scrape_path}). Is `/loop 30s /watch-draft` running?")
            return
        if feed_age > 120:
            st.warning(f"Draft-room feed is stale ({feed_age:.0f}s old). Is the /watch-draft loop still running?")
    st.caption(f"{label} polling every {ss().poll_interval}s · last update {age}")


def maybe_snapshot(recs):
    state: DraftState = ss().state
    if not state.on_the_clock or not recs:
        return
    pick = state.current_pick
    if pick in ss().snapshots_done:
        return
    try:
        d = Path(ss().paths["snapshots_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / f"pick-{pick}.json").write_text(json.dumps(snapshot(state, recs), indent=1))
        ss().snapshots_done.add(pick)
        top = recs[0]
        ss().log.info("On the clock at pick %d: #1 %s (%s) score %.1f conf %s", pick, top.player.name,
                      top.player.position, top.adjusted_score, top.confidence)
    except Exception as exc:  # noqa: BLE001
        ss().log.error("Snapshot failed: %s", exc)


def draft_state_panel():
    state: DraftState = ss().state
    st.subheader(f"Round {state.current_round}" if not state.is_complete else "Draft complete")
    c1, c2 = st.columns(2)
    c1.metric("Current pick", state.current_pick if not state.is_complete else "—")
    nxt = state.next_user_pick()
    c2.metric("Your next pick", nxt if nxt else "—")
    c1.metric("Picks away", state.picks_until_user if state.picks_until_user is not None else "—")
    on_clock = state.slot_for_pick(state.current_pick) if not state.is_complete else None
    c2.metric("On the clock", state.team_name(on_clock) if on_clock else "—")
    if state.on_the_clock:
        st.success("**YOU ARE ON THE CLOCK**")
    status = {"manual": "Manual mode (no Yahoo)", "connected": "Yahoo: connected", "lost": "Yahoo: SYNC LOST"}[state.sync_status]
    age = f"{time.time() - state.last_sync:.0f} s ago" if state.last_sync else "never"
    (st.error if state.sync_status == "lost" else st.caption)(f"{status} · last update {age}")
    run = state.last_positions(ss().by_id, 6)
    if run:
        st.caption("Last 6 picks: " + " ".join(run))


def roster_panel():
    state: DraftState = ss().state
    st.subheader("Your team")
    roster_players = [ss().by_id[pid] for pid in state.user_roster_ids() if pid in ss().by_id]
    unknown = [p for p in state.picks if p.slot == state.user_slot and p.player_id not in ss().by_id]
    slots = assign_roster_slots(roster_players, state.settings.roster)
    lines = []
    for pos, n in state.settings.roster.slots.items():
        got = slots.get(pos, [])
        names = [f"{p.name} ({p.projected_points:.0f})" for p in got]
        for i in range(n):
            lines.append(f"**{pos}** {names[i] if i < len(names) else '—'}")
        for extra in names[n:]:
            lines.append(f"**{pos}+** {extra}")
    for u in unknown:
        lines.append(f"**?** {u.player_name or u.player_id}")
    st.markdown("  \n".join(lines))


def recommendations_panel(recs):
    state: DraftState = ss().state
    st.subheader("Recommendations")
    if not recs:
        st.info("No more picks for you." if state.next_user_pick() is None else "Nothing to recommend.")
        return
    top_n = int(ss().ocfg["top_n"])
    conf = recs[0].confidence
    if conf == "CLOSE":
        st.warning("CLOSE DECISION between the top options")
    elif conf == "MODERATE":
        st.info("Moderate edge for the top option")
    my_pick = state.next_user_pick()
    for i, r in enumerate(recs[:top_n], start=1):
        p = r.player
        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            h1.markdown(f"### {i}. {p.name} — {p.position} {p.team}")
            h2.markdown(f"**{r.action}**  \n{RISK_ICON.get(p.risk_label, p.risk_label)}")
            m = st.columns(5)
            m[0].metric("Score", f"{r.adjusted_score:.0f}")
            m[1].metric("Value", f"{r.value:.0f}", help=f"VOR {r.vor:.0f}" + (f", ext VBD {p.external_vbd_scaled:.0f}" if p.external_vbd_scaled is not None else ""))
            m[2].metric("Yahoo ADP", f"{p.adp:.0f}" if p.adp is not None else "—")
            fol = state.following_user_pick()
            m[3].metric(f"Avail @{fol}" if fol else "Survival", f"{r.survival:.0%}" if fol else "—")
            m[4].metric("Wait cost", f"{r.wait_cost:.0f}")
            detail = f"Proj {p.projected_points:.0f} · Tier {p.tier} {p.position}"
            if not state.on_the_clock and my_pick:
                detail += f" · {r.availability:.0%} chance he reaches your pick #{my_pick}"
            st.caption(detail)
            with st.expander("Why?"):
                st.markdown("\n".join(f"- {b}" for b in r.reasons))
            b1, b2, _ = st.columns([1, 1, 2])
            if state.on_the_clock:
                if b1.button("✓ I drafted him", key=f"take_{p.player_id}"):
                    do_manual_pick(p, state.user_slot, state.current_pick)
                    st.rerun()
            else:
                if b1.button("Taken by other", key=f"gone_{p.player_id}", help="Mark as drafted by the team on the clock"):
                    do_manual_pick(p, None, None)
                    st.rerun()


def conflicts_panel():
    state: DraftState = ss().state
    st.error("SYNC WARNING — local picks disagree with the remote draft results")
    for c in list(state.conflicts):
        remote = "Draft room" if c.source == "scrape" else "Yahoo"
        cols = st.columns([3, 1, 1])
        cols[0].markdown(f"**Pick {c.pick}** · Local: {name_of(c.local_player_id, c.local_player_name)} · "
                         f"{remote}: {name_of(c.yahoo_player_id, c.yahoo_player_name)}")
        if cols[1].button("Keep local", key=f"kl_{c.pick}"):
            state.resolve_conflict(c.pick, "local")
            save_state(); ss().log.info("Conflict at pick %d resolved: keep local", c.pick); st.rerun()
        if cols[2].button(f"Use {remote}", key=f"ky_{c.pick}"):
            state.resolve_conflict(c.pick, "yahoo")
            save_state(); ss().log.info("Conflict at pick %d resolved: use %s", c.pick, remote); st.rerun()


def available_table(recs):
    state: DraftState = ss().state
    st.subheader("Available players")
    f1, f2, f3 = st.columns([2, 2, 2])
    pos = f1.pills("Position", ["ALL", "QB", "RB", "WR", "TE", "FLEX", "K", "DEF"], default="ALL", key="pos_filter") or "ALL"
    risk = f2.pills("Risk", ["ALL", "SAFE", "BALANCED", "BOOM-BUST"], default="ALL", key="risk_filter") or "ALL"
    query = f3.text_input("Search", placeholder="player name…")
    q = query.strip().lower()
    rows = []
    fol = state.following_user_pick()
    for rank, r in enumerate(recs, start=1):
        p = r.player
        if pos == "FLEX" and p.position not in state.settings.roster.flex_positions:
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
            "Yahoo ADP": p.adp, "Reach my pick": None if state.on_the_clock else r.availability,
            "Survival": r.survival if fol else None, "Wait cost": r.wait_cost,
            "Need": r.roster_need, "Score": r.adjusted_score,
            "Risk": p.risk_label, "Rank σ": p.rank_stddev,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No players match.")
        return
    if state.on_the_clock:
        df = df.drop(columns=["Reach my pick"])
    st.dataframe(
        df, hide_index=True, use_container_width=True, height=520,
        column_config={
            "Proj": st.column_config.NumberColumn(format="%.0f"),
            "VOR": st.column_config.NumberColumn(format="%.0f"),
            "Ext VBD": st.column_config.NumberColumn(format="%.0f"),
            "Value": st.column_config.NumberColumn(format="%.0f"),
            "Yahoo ADP": st.column_config.NumberColumn(format="%.1f"),
            "Reach my pick": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1),
            "Survival": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1,
                                                        help="P(still available at your following pick)"),
            "Wait cost": st.column_config.NumberColumn(format="%.1f"),
            "Need": st.column_config.NumberColumn(format="%.2f"),
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "Rank σ": st.column_config.NumberColumn(format="%.1f", help="Expert rank std dev (external)"),
        },
    )


def manual_controls():
    state: DraftState = ss().state
    st.subheader("Mark player drafted")
    drafted = state.drafted_ids()
    avail = sorted((p for p in ss().players if p.player_id not in drafted),
                   key=lambda p: (p.adp if p.adp is not None else 999, -p.projected_points))
    by_id = ss().by_id
    with st.form("manual_pick", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        choice = c1.selectbox("Player", [p.player_id for p in avail], index=None, placeholder="type to search…",
                              format_func=lambda pid: player_label(by_id[pid]))
        slot_opts = sorted(t.slot for t in state.teams) or list(range(1, state.num_teams + 1))
        default_slot = state.slot_for_pick(state.current_pick) if not state.is_complete else slot_opts[0]
        default_idx = slot_opts.index(default_slot) if default_slot in slot_opts else 0
        slot = c2.selectbox("Drafted by", slot_opts, index=default_idx,
                            format_func=lambda s: f"{s}. {state.team_name(s)}" + (" (you)" if s == state.user_slot else ""))
        pick_no = c3.number_input("Pick", 1, state.total_picks, min(state.current_pick, state.total_picks))
        submitted = c4.form_submit_button("Mark drafted", use_container_width=True)
    if submitted:
        if choice is None:
            st.error("Pick a player first.")
        else:
            do_manual_pick(by_id[choice], int(slot), int(pick_no))
            st.rerun()
    u1, u2, _ = st.columns([1, 1, 4])
    if u1.button("Undo last pick", use_container_width=True, disabled=not state.picks):
        do_undo()
        st.rerun()


def history_panel():
    state: DraftState = ss().state
    with st.expander(f"Draft history ({len(state.picks)} picks)", expanded=False):
        if not state.picks:
            st.caption("No picks yet.")
            return
        rows = []
        for p in sorted(state.picks, key=lambda x: x.pick, reverse=True):
            pl = ss().by_id.get(p.player_id)
            rows.append({"Pick": p.pick, "Round": p.round, "Team": state.team_name(p.slot),
                         "Player": pl.name if pl else (p.player_name or p.player_id),
                         "Pos": pl.position if pl else "?", "Source": p.source + (" ✓" if p.confirmed else "")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=300)


# --------------------------------------------------------------------------- #
# Readiness page
# --------------------------------------------------------------------------- #


def readiness_page():
    state: DraftState = ss().state
    players = ss().players
    st.header("Draft readiness")
    client = ss().yahoo
    authed = client is not None and client.has_token
    have_yahoo_players = bool(ss().yahoo_players)
    matched = len(ss().mapping) if have_yahoo_players else 0
    with_adp = sum(1 for p in players if p.adp is not None)
    with_ext = sum(1 for p in players if p.external_vbd is not None or p.rank_stddev is not None)
    checks = [
        ("Yahoo authentication", authed, "Authorize in the sidebar (needs .env credentials)"),
        ("League settings", bool(state.settings.league_key), "Load league in the sidebar; config.yaml defaults in use"),
        ("Teams", bool(state.settings.league_key) and bool(state.teams), f"{len(state.teams)} teams"),
        ("User team", any(t.is_user for t in state.teams), f"slot {state.user_slot}"),
        ("Player projections", bool(players), f"{len(players)} players loaded"),
        ("Yahoo player mapping", have_yahoo_players and not ss().unmatched, f"{matched} matched, {len(ss().unmatched)} unmatched"),
        ("ADP", with_adp >= max(50, len(players) // 2), f"{with_adp} of {len(players)} players have ADP"),
        ("External VBD / risk data", with_ext > 0, f"{with_ext} players carry external data (optional)"),
        ("Draft state persistence", Path(ss().paths["draft_state"]).exists() or not state.picks, ss().paths["draft_state"]),
    ]
    for label, ok, note in checks:
        st.markdown(f"{'✅' if ok else '❌'} **{label}** — {note}")

    st.divider()
    st.subheader("Yahoo player pool & ADP")
    c1, c2, c3 = st.columns(3)
    if c1.button("Fetch Yahoo player pool (~20 requests)", disabled=not (authed and ss().yahoo_league_key)):
        prog = st.progress(0, text="Fetching…")
        try:
            rows = yh.fetch_all_players(client, ss().yahoo_league_key, max_players=600,
                                        progress=lambda n: prog.progress(min(n / 600, 1.0), text=f"{n} players"))
            yh.save_yahoo_players(rows, ss().paths["yahoo_players_csv"])
            ss().yahoo_players = rows
            refresh_mapping()
            st.success(f"Fetched {len(rows)} players; {len(ss().mapping)} mapped")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fetch failed: {exc}")
    if c2.button("Fill missing ADP from Yahoo", disabled=not have_yahoo_players):
        n = yh.fill_adp_from_yahoo(players, ss().yahoo_players)
        reprepare()
        st.success(f"Filled ADP for {n} players")
    if c3.button("Overwrite all ADP with Yahoo", disabled=not have_yahoo_players):
        n = yh.fill_adp_from_yahoo(players, ss().yahoo_players, overwrite=True)
        reprepare()
        st.success(f"Set ADP for {n} players")
    if have_yahoo_players:
        st.caption(f"{len(ss().yahoo_players)} Yahoo players cached in {ss().paths['yahoo_players_csv']}")

    st.subheader(f"Unmatched players: {len(ss().unmatched)}")
    if ss().unmatched:
        st.dataframe(pd.DataFrame([{"Player": p.name, "Pos": p.position, "Team": p.team, "Proj": p.projected_points,
                                    "ADP": p.adp} for p in ss().unmatched]), hide_index=True, use_container_width=True, height=240)
        st.markdown("**Save a manual mapping**")
        c1, c2, c3 = st.columns([2, 3, 1])
        unmatched_by_id = {p.player_id: p for p in ss().unmatched}
        local_id = c1.selectbox("Local player", list(unmatched_by_id),
                                format_func=lambda pid: f"{unmatched_by_id[pid].name} ({unmatched_by_id[pid].position} {unmatched_by_id[pid].team})")
        local = unmatched_by_id.get(local_id)
        ypool = {r["yahoo_player_id"]: r for r in ss().yahoo_players if local is None or r["position"] == local.position}
        yid = c2.selectbox("Yahoo player", list(ypool), index=None, placeholder="type to search…",
                           format_func=lambda i: f"{ypool[i]['name']} ({ypool[i]['position']} {ypool[i]['team']}) · ADP {ypool[i]['adp'] or '—'}")
        yrow = ypool.get(yid)
        if c3.button("Save mapping") and local and yrow:
            manual = load_mappings(ss().paths["mappings_csv"])
            manual[local.player_id] = yrow["yahoo_player_id"]
            save_mappings(ss().paths["mappings_csv"], manual, names={local.player_id: local.name})
            refresh_mapping()
            ss().log.info("Manual mapping saved: %s -> %s", local.name, yrow["yahoo_player_id"])
            st.success(f"Mapped {local.name} → {yrow['name']}")
            st.rerun()
    elif not have_yahoo_players:
        st.caption("Fetch the Yahoo player pool to check mappings.")

    if ss().external_reports:
        st.subheader("External imports")
        for rep in ss().external_reports:
            st.markdown(f"- **{rep['file']}** ({rep['format']}): {rep['matched']} / {rep['rows']} matched"
                        + (f"; unmatched: {', '.join(rep['unmatched'][:10])}" if rep["unmatched"] else ""))

    st.subheader("Replacement levels (league-specific)")
    if ss().get("replacement"):
        st.dataframe(pd.DataFrame([{"Pos": k, "Replacement pts": round(v, 1)} for k, v in ss().replacement.items()]),
                     hide_index=True)


# --------------------------------------------------------------------------- #


init()
sidebar()
if ss().page == "Draft":
    draft_page()
else:
    readiness_page()
