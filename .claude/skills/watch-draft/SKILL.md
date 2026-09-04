---
name: watch-draft
description: One tick of the draft-room watcher. Reads the picks made so far off the Yahoo draft-room tab (Claude in Chrome) and writes them to scrape/picks.json for the Streamlit optimizer. Run as `/loop 30s /watch-draft` for the whole draft. Use when the user says "watch the draft", "start the draft watcher", or invokes /watch-draft.
---

# /watch-draft — one tick

The Streamlit app (`streamlit run app.py`, sidebar "Draft room (Claude in Chrome)" toggled on)
polls `scrape/picks.json`. Your job each tick: read the draft room page, write the full list of
picks made so far. The file is a snapshot, so rewriting the same content is harmless.

## Steps

1. **Find the tab.** If the Chrome tools are not loaded, load them in ONE ToolSearch call:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__find,mcp__claude-in-chrome__computer`.
   Call `tabs_context_mcp` and pick the tab whose URL is on `football.fantasysports.yahoo.com`
   and whose title or URL contains `draft`. Remember its tab id across ticks. Never open a new tab
   or navigate the draft tab: the user is drafting in it.
2. **Read the picks.** Call `get_page_text` on that tab. Locate the draft results / "Picks" /
   "Draft Results" section: a list of `pick number · team name · player name · position · NFL team`
   entries (Yahoo shows them as e.g. `1. Team Gronk — Bijan Robinson (Atl - RB)`). If the results
   panel is on a tab not currently visible, use `find` for "Draft Results" or "Picks" and click it
   once with `computer`, then read again. Do not scroll or click anything else.
3. **Compare to last tick.** Keep the pick count from the previous tick in your head. If it has
   not changed, write nothing and end the tick with a one-line status.
4. **Write the feed.** Emit one line per pick and pipe it to the writer (repo root as cwd):

   ```bash
   .venv/bin/python write_picks.py <<'PICKS'
   1 | Team Gronk | Bijan Robinson | RB | ATL
   2 | Dad Bods | Ja'Marr Chase | WR | CIN
   PICKS
   ```

   Fields: `pick | fantasy team name | player | position | NFL team`. Team name exactly as the
   page shows it (the app maps team names to draft slots from round 1 and uses "My team name"
   from the sidebar to find the user's slot). Position as `QB/RB/WR/TE/K/DEF`; Yahoo's `D/ST` or
   `DEF` both map to DEF. Include every pick made so far, not just the new ones.
5. **Report** one line: `tick: N picks (M new), last: <player>`.

## Stop conditions

End the loop (reply with the stop instruction for /loop) when:
- the page says the draft is complete / all rounds are filled, or
- the draft tab is gone, or
- three consecutive ticks find no draft-results section (say so; the user may have navigated away).

## Do not

- Do not pick players, click "Draft", or interact with the queue. Read only.
- Do not guess a pick you cannot read; leave it out and mention it in the tick report.
- Do not edit `scrape/picks.json` by hand; always go through `write_picks.py`.
