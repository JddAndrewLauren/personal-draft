# Yahoo Fantasy Football Draft Optimizer — Product Plan

(Condensed from the original specification; section numbers match the original so it can be
used as the project's reference. The original wording is preserved wherever it is normative.)

## 1. Project Goal

Build a small, local tool that assists with a live Yahoo Fantasy Football snake draft.

The application runs locally on a Mac alongside the Yahoo draft room. It reads the current draft state from Yahoo when possible, combines that state with preseason projections and Yahoo draft-market information, and continuously recommends the best players to draft.

The central question is not simply:

«Who is the highest-ranked available player?»

It is:

«Given my roster, the players still available, my next pick, and the probability that each player will still be available then, which player creates the most value if I draft them now?»

The tool should remain deliberately narrow.

It is not intended to become a fantasy-football management platform, league website, AI agent, projection system, or replacement for Yahoo.

Its purpose is to make better decisions during one specific draft.

## 2. Design Principles

### 2.1 Keep the application small

Prefer simple implementations over generalized infrastructure.

Avoid unless demonstrated to be necessary: databases, Docker, cloud hosting, user accounts, mobile support, background services, JavaScript frameworks, LLM integration, writing to Yahoo, complex historical analytics, distributed components.

The target environment is one Mac used by one person.

### 2.2 Yahoo is primarily a source of draft state

The application should not attempt to control the Yahoo draft. Yahoo is used to determine: league settings, teams, draft order when available, completed draft picks, player IDs, which players are no longer available, which players belong to the user's roster.

The optimizer remains completely local. No Yahoo write operations are required.

### 2.3 Never make the optimizer dependent on Yahoo live polling

Yahoo draft-result polling should be the preferred source of live state, but the application must remain usable if Yahoo updates slowly, caches draft results, temporarily fails, changes API behavior, or stops returning results during the draft.

Therefore the local application maintains its own draft state. Yahoo synchronization updates that state automatically. The user can always manually mark a player as drafted.

### 2.4 Separate player quality from acquisition cost

**Player quality** is primarily determined from fantasy projections, league scoring, positional replacement value, positional tiers.

**Acquisition cost** is primarily determined from Yahoo ADP, current pick, next user pick, expected draft behavior, probability the player survives.

A player can therefore be extremely valuable without necessarily being the correct player to draft immediately.

## 3. Primary User Experience

```
cd fantasy-draft
source .venv/bin/activate
streamlit run app.py
```

The browser opens http://localhost:8501. Yahoo's draft room can be open in another browser window. The application continuously refreshes draft state and recommendations.

## 4. Main Screen

Information density over elaborate visual design. Three primary areas:

### 4.1 Current Draft State

Current round, current overall pick, drafting team, user's next pick, picks until user's next pick, Yahoo synchronization status, timestamp of last successful synchronization.

### 4.2 User Roster

The user's drafted players grouped by position (QB, RB, WR, TE, FLEX, BENCH). Roster needs should be visually obvious.

### 4.3 Recommendations

Top 3–5 recommendations prominently, e.g.

```
1. PLAYER A — RB
Recommendation Score       82.4
Projected Points            274
VOR                           58
Yahoo ADP                   53.2
Chance available at 57       12%
Expected wait cost            21
TAKE NOW
```

The user should understand the recommendation within several seconds.

## 5. Available Player Table

Columns: Rank, Player, Pos, Team, Projection, VOR, Tier, Yahoo ADP, Survival, Wait Cost, Score. Sort by any column; filter by QB/RB/WR/TE/FLEX; search field.

## 6. Manual Draft Control

Manual state management is an important fallback rather than an afterthought. Provide "Mark Player Drafted" (player search, optional team, pick) which immediately removes the player from the available pool, and "Undo Last Pick".

## 7. Yahoo Integration — Authentication

Yahoo OAuth. Credentials live locally in `.env`. OAuth tokens cached locally. Authentication completed and tested well before draft day.

## 8. Yahoo Data

Retrieve only what is needed: league settings (scoring, roster, number of teams, identity), teams (user's team, draft order, roster ownership), draft results (pick, round, team_key, player_key). Map Yahoo player IDs to the local player database.

## 9. Yahoo Polling

Every ~5 s (configurable): request draft results, compare with local state, identify new picks, add them, remove drafted players, recompute recommendations.

## 10. Synchronization Behavior

Yahoo never blindly overwrites local state. Maintain local draft state and last Yahoo state; merge additional Yahoo picks. Manual picks remain valid until Yahoo confirms or contradicts them. Conflicts are surfaced (SYNC WARNING: local vs Yahoo at pick N) and the user selects the correct state.

## 11. Player Data

Normalized local player table: player_id, yahoo_player_id, name, team, position, projected_points, adp, adp_stddev, tier. Minimum: name, position, projected_points, yahoo_adp.

## 12. Projection Source

Do not build a projection model. Import a CSV (`Player,Position,Team,ProjectedPoints,YahooADP`). Projections must reflect the league's scoring; if the source has raw stats, compute points from Yahoo's scoring configuration.

## 13. Player Identity Mapping

Preferred key: Yahoo player ID. Fallback: normalized name + NFL team + position (normalize punctuation, suffixes, apostrophes, periods, capitalization; "D.J. Moore" == "DJ Moore"). Show unresolved players before draft day; save manual mappings. No fuzzy matching during the live draft.

## 14–15. Baseline Valuation and Replacement Levels

VOR(player) = projected_points(player) − projected_points(replacement at position). Replacement levels derive from roster configuration (e.g. 12 teams: QB13, RB30, WR36, TE13), not hard-coded.

## 16. Positional Tiers

Group players into value tiers from projection gaps to identify cliffs.

## 17–19. Draft Availability Model

For every available player estimate P(survives until my next pick) from Yahoo ADP treated as a distribution (normal/logistic approximation with configurable variance): survival(p, next_pick) = P(draft_position(p) ≥ next_pick). Display it directly.

## 20. Expected Replacement at the Next Pick

Compute expected_best_available(position, next_pick) from survival probabilities so that waiting on a deep position is cheap and missing a lone elite TE is expensive.

## 21. Wait Cost

WaitCost = P(unavailable next pick) × (VOR − expected replacement VOR next pick).

## 22. Recommendation Score

RecommendationScore = VOR + λ × WaitCost, λ = 1 initially, configurable.

## 23–25. Roster Constraints, Roster Need, FLEX

Hard constraints prevent impossible recommendations; soft constraints influence them. AdjustedScore = RecommendationScore × RosterNeed(position): starter missing 1.10, normal 1.00, heavily filled 0.70, impossible 0. FLEX handled approximately (RB/WR/TE compete for FLEX demand); no combinatorial optimization initially.

## 26–27. Explanation and Confidence

Explain recommendations with deterministic facts from the optimizer's calculations (no LLM). Classify STRONG / MODERATE / CLOSE from the gap between top candidates.

## 28–29. Draft History and Positional Runs

Maintain the complete local draft (pick, round, team, player, pos). Recognizing runs is optional initially.

## 30–31. Architecture and Responsibilities

```
app.py, yahoo.py, optimizer.py, models.py, data/players.csv, data/player_mappings.csv,
tests/test_optimizer.py, tests/test_draft_state.py, .env, .gitignore, requirements.txt, README.md, plan.md
```

app.py: Streamlit UI, application state, polling, recommendations, manual controls.
yahoo.py: OAuth, requests, settings, teams, draft results, translation to internal models; no optimizer logic.
optimizer.py: replacement values, VOR, tiers, survival, expected alternatives, wait cost, roster need, ranking; knows nothing about Yahoo.
models.py: Player, DraftPick, Team, LeagueSettings, DraftState, Recommendation (dataclasses).

## 32. State Management

No database. Draft state in memory, saved to `draft_state.json` after every pick for recovery.

## 33–35. Testing Strategy, Replay, Sanity Tests

The optimizer must be testable independently of Yahoo; a recorded draft should be replayable (`python replay_draft.py test-data/draft.json`). Sanity tests: elite player falls → recommendation rises; QB filled → QB pressure decreases; tier cliff → wait cost rises; deep WR tier → low urgency; 95 % survival → prefer waiting.

## 36–38. Draft-Day Reliability, Graceful Yahoo Failure, Readiness Screen

Before the draft verify auth, token refresh, league, mappings, projections, IDs, draft results, manual drafting, undo, persistence; run a mock draft. If Yahoo fails show "YAHOO SYNC LOST — manual draft mode active" and keep recommending. Provide a DRAFT READINESS report (auth, settings, teams, user team, projections, mapping, ADP; players loaded/matched/unmatched).

## 39–45. Phases

1. Offline optimizer from static files (complete mock draft manually).
2. Roster awareness.
3. Yahoo read integration (reconstruct a completed draft).
4. Live Yahoo synchronization (polling); keep manual entry if Yahoo is unreliable.
5. Draft-day UI polish (fast scanning, big names, dense table, sync health).
6. Monte Carlo optimization only if the analytical optimizer shows obvious limitations (simulate remaining draft; opponents = ADP + randomness with roster requirements).

## 46–50. Future

Historical opponent modeling, positional run detection, projection uncertainty (floor/ceiling), multiple projection sources / consensus, playoff-week weighting.

## 51. Explicit Non-Goals

Waiver recommendations, trade analysis, weekly lineups, matchup projections, chat, AI commentary, draft automation, automatic player selection, mobile, cloud, multi-user, league management, generalized platform.

## 52–55. Technology, Dependencies, Configuration, Observability

Python + Streamlit + pandas; CSV/JSON storage; dependencies: streamlit, pandas, numpy, requests, python-dotenv (scipy optional). Small config file (league teams, user slot, rounds, wait_cost_weight, adp_stddev, polling interval); Yahoo-derived settings override manual values. Local log `fantasy-draft.log` with Yahoo requests/errors, sync events, picks, manual corrections, recommendations at each user pick.

## 56–58. Snapshots, Performance, Failure Philosophy

Save `snapshots/pick-N.json` whenever the user is on the clock. Recommendations recalculate in < 500 ms (Monte Carlo < 2 s). Expose uncertainty (CLOSE DECISION) rather than false precision.

## 59–62. Core Metric, MVP, Development Order, Final Scope

VALUE NOW + COST OF WAITING, not highest projection or highest ADP. MVP: launch, load league, load projections + ADP, show draft state, auto-identify drafted players, manual corrections, league-specific VOR, survival, wait cost, top 3–5 with explanations, immediate updates. Build in order: player CSV → manual board → VOR → survival → wait cost → roster awareness → UI → replay → tune → Yahoo auth → ingestion → polling → polish; Monte Carlo and opponent models only afterwards. The result is a small decision-support instrument running beside Yahoo.

## Additions agreed during implementation

- Import external VBD / ranking data from outside Yahoo (FantasyPros projections and ECR exports, or a generic column-mapped CSV) and blend it with the league-specific VOR using a configurable weight.
- Label players SAFE / BALANCED / BOOM-BUST from expert-rank disagreement (rank standard deviation and best/worst spread relative to positional peers) as an informational risk/reward signal.
