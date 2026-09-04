# Yahoo Fantasy Football Draft Optimizer

A small local Streamlit tool that runs beside the Yahoo draft room and answers one question at
every pick:

> Given my roster, the players still available, my next pick, and the probability that each
> player is still there then, which player creates the most value if I draft him now?

It combines league-specific **value over replacement (VOR)** with a Yahoo-ADP **availability
model** to compute the **expected cost of waiting** on each player, then ranks players by
`value + wait cost`, adjusted for roster need. Optional external data (FantasyPros exports)
blends in an outside VBD opinion and labels players **SAFE / BALANCED / BOOM-BUST** from
expert-rank disagreement.

Yahoo is used **read-only** as a source of draft state. Everything works without Yahoo in manual
mode; Yahoo sync just fills picks in automatically.

The full product spec lives in [plan.md](plan.md).

## Setup (Mac)

```bash
cd personal-draft
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The browser opens at <http://localhost:8501>. Keep the Yahoo draft room in another window.

`data/players.csv` ships with **placeholder data**: real player names but synthetic projections,
ADP and rank data so a mock draft works out of the box. Replace it with real projections before
your draft (see below).

## Player data

### Master table: `data/players.csv`

Minimum columns:

```
Player,Position,Team,ProjectedPoints,YahooADP
```

Optional: `ADPStdDev`, `YahooPlayerID`, `Bye`, `VBD`, `ExternalADP`, `RankAvg`, `RankBest`,
`RankWorst`, `RankStdDev`, `ExternalTier`. Column names are matched loosely (`Pos`, `Tm`,
`FPTS`, `ADP` all work). Lines starting with `#` are ignored.

If the file has raw stat columns (`PassYds, PassTD, Int, RushYds, RushTD, Rec, RecYds, RecTD,
FumLost`) and no points column, points are computed from the league scoring (Yahoo's scoring
once a league is loaded, otherwise Yahoo's half-PPR defaults).

Projections **must reflect your league's scoring** (PPR vs half-PPR etc.).

### External VBD / rankings (sidebar → "External VBD / rankings CSV")

Supported out of the box:

- **FantasyPros ECR / rankings export** (`RK, TIERS, PLAYER NAME, TEAM, POS, BEST, WORST, AVG.,
  STD.DEV, ECR VS. ADP`). Provides expert-rank disagreement (→ risk labels) and an external
  rank-based VBD. Overall exports carry overall ranks; a single-position export is detected and
  its ranks treated as positional.
- **FantasyPros projections export** (`Player, Team, …, FPTS`). Overwrites projected points.
  Pick the position in the sidebar for single-position exports.
- **Generic CSV**: map columns under `import.column_map` in `config.yaml`.

Rows are matched by normalised name + position (team breaks ties). No fuzzy matching, so
check the Readiness page for unmatched rows and fix names in the CSV.

"Save enriched CSV" writes the merged table back to `data/players.csv` so you don't need to
re-import on draft day.

### How value is computed

- `VOR = projected points − projection of the replacement player` at that position. The
  replacement rank is derived from the roster: `teams × starters + teams × FLEX × flex weight + 1`
  (12 teams, 2 RB, 1 FLEX split 50/50 with WR → RB31). Kicker/defense VOR is discounted 50 %
  (`position_value_scale`) because those projections are mostly noise.
- If external VBD exists, `value = (1 − w) × VOR + w × external VBD` with the external numbers
  linearly rescaled onto the VOR scale. `w` is `external_vbd_weight` (default 0.5, slider in the
  sidebar; 0 = ignore external data). Players without external data use VOR alone.
- Tiers: a new tier starts at a projection gap ≥ `tier_gap_points` or when the drop from the
  tier's top exceeds `tier_width_points`.

### Availability, wait cost, score

- Draft position ~ Normal(ADP, σ), σ = `adp_stddev + adp_stddev_per_pick × ADP` (or the
  player's own `ADPStdDev`). Survival is **conditional** on the player still being available
  now, so a faller with ADP 30 on the board at 45 is not written off.
- `expected fallback = Σ value_i · s_i · Π(1 − s_j)` over better-first alternatives at the same
  position (excluding the player), i.e. what you likely end up with if you wait.
- `wait cost = (1 − survival) × max(0, value − expected fallback)`.
- `score = value + λ × wait cost`, then `× roster need` (starter open 1.10, FLEX open 1.00,
  bench 0.85, deep bench 0.70, position capped 0). When you are not on the clock, the score is
  also multiplied by the chance the player reaches your pick.
- Confidence: STRONG / MODERATE / CLOSE from the gap between the top two adjusted scores.

### Risk labels

With expert-rank data (`RankStdDev`, `RankBest`, `RankWorst`): the rank standard deviation is
normalised by rank depth, z-scored within the position, and labelled SAFE (≤ −0.5),
BALANCED, or BOOM-BUST (≥ +0.5). Labels are informational (a table column, a filter, a badge
on each recommendation, and a line in "Why?"); they do not change the score.

## Draft-day workflow

1. **Readiness page** (sidebar): every row should be ✅. Check unmatched players, ADP coverage,
   and that the roster shown matches your league.
2. **Draft page**: three panels on top (draft state, your roster, top recommendations with a
   "Why?" explanation and a badge such as TAKE NOW / NOW OR NEVER / CLOSE DECISION), the
   sortable/filterable available-player table, and manual controls below.
3. **When you are on the clock** the top card shows "✓ I drafted him". At any time you can mark
   any player as taken with "Taken by other" or the "Mark player drafted" form (player, team,
   pick number). **Undo last pick** reverts mistakes.
4. State is saved to `draft_state.json` after every pick and restored on restart. A snapshot
   of the recommendations is written to `snapshots/pick-N.json` each time you are on the clock,
   and events go to `fantasy-draft.log`.

## Yahoo integration

1. Create an app at <https://developer.yahoo.com/apps/create/>: Confidential Client, redirect
   URI `https://localhost:8501/` (Yahoo rejects `oob`). The create form no longer offers a
   Fantasy Sports permission: since 2026 Yahoo gates the Fantasy API behind an application at
   <https://sports.yahoo.com/developer/access/> (personal / single-league use is an accepted
   category; give them the app's Client ID). Until it is approved, authorization fails with
   `invalid_scope` and the app stays in manual mode.
2. Put the Client ID / Secret in 1Password (`dev` vault, item `personal-draft-yahoo`) and run
   `op run --env-file=.env.template -- .venv/bin/streamlit run app.py`. Without `op`, copy
   `.env.example` to `.env` and fill in `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET`.
3. In the sidebar: **Authorize with Yahoo** (opens Yahoo). After you approve, the browser lands
   on an `https://localhost:8501/?code=...` page that will not load; copy that URL from the
   address bar, paste it into the sidebar, **Exchange code**. The token is cached in `.yahoo_token.json` and refreshed automatically.
4. **Fetch my leagues → pick one → Load league settings & teams.** Roster slots, FLEX
   eligibility, team count and scoring now come from Yahoo. Your draft slot is taken from
   Yahoo once round 1 has started (Yahoo does not publish the order before that); set it
   manually in the sidebar until then.
5. Readiness page → **Fetch Yahoo player pool** (about 20 requests, cached in
   `data/yahoo_players.csv`). This resolves Yahoo player IDs and lets you **fill missing ADP
   from Yahoo**. Save manual mappings for anything unmatched (stored in
   `data/player_mappings.csv`).
6. During the draft toggle **Live sync**. Every N seconds (default 5) the app reads
   `draftresults`, appends new picks, confirms manual picks, and raises a **SYNC WARNING** if
   Yahoo disagrees with a manual pick (you choose which to keep). If Yahoo fails, the app shows
   **YAHOO SYNC LOST** and keeps working in manual mode.

Do all of this well before draft day and run at least one Yahoo mock draft with the app open.

## Draft day without API access (Claude in Chrome watches the draft room)

If the Yahoo application is not approved by draft day, Claude Code can read the draft room page
itself and feed picks to the app. Nothing Yahoo-specific is required.

1. Open the Yahoo draft room in Chrome (logged in, Claude in Chrome extension enabled for the site).
2. `streamlit run app.py`. In the sidebar under **Draft room (Claude in Chrome)** toggle
   **Watch scrape feed** and enter **My team name** exactly as the draft room shows it.
3. In Claude Code, from the repo root, run `/loop 30s /watch-draft`. Every 30 s Claude reads the
   draft results off the page and writes `scrape/picks.json` (via `write_picks.py`). The app
   polls that file, appends new picks, confirms manual picks, and raises the same **SYNC WARNING**
   on disagreement. Your draft slot auto-corrects from round 1 once your team name appears.
4. If the app shows "feed is stale", the loop has stopped: re-run the `/loop` command. Manual
   picks keep working throughout.

Rehearse in a Yahoo **mock draft** first: mock rooms are available any time and use the same page.

## Testing and replay

```bash
pytest -q                                    # optimizer, draft state, importers, Yahoo parsers
python replay_draft.py test-data/draft.json   # replay a recorded draft, print recs at each user pick
python replay_draft.py test-data/draft.json --all   # also time a recompute after every pick
```

The replay reports how often the recorded pick was the optimizer's #1 / top-3 and the maximum
recompute time (target < 500 ms; typically a few ms). To replay a real Yahoo draft, export the
picks into the same JSON shape (`teams, rounds, user_slot, picks[{pick, player, position}]`).

## Configuration

`config.yaml` holds league defaults (used until Yahoo settings are loaded), your draft slot,
optimizer parameters (λ, ADP σ, external VBD weight, flex weights, tier thresholds, roster-need
multipliers, confidence and risk thresholds), the polling interval and file paths. The most
useful knobs are also sliders in the sidebar.

## Layout

```
app.py            Streamlit UI, session state, polling, manual controls, readiness page
optimizer.py      VOR, tiers, VBD blend, risk labels, survival, wait cost, roster need, ranking
models.py         dataclasses, DraftState (snake order, undo, Yahoo merge, persistence), CSV loaders
yahoo.py          OAuth2, Yahoo requests, XML parsing, player mapping
replay_draft.py   replay a recorded draft through the optimizer
data/             players.csv, player_mappings.csv (yahoo_players.csv cached here)
test-data/        recorded draft + FantasyPros and Yahoo fixtures
tests/            pytest suites
```

Not in scope (by design): auction drafts, waiver/trade/lineup advice, writing to Yahoo,
multi-user or hosted deployment, Monte Carlo roster simulation (a possible later phase).
