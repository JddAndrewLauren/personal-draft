---
name: watch-draft
description: One tick of the draft-room watcher. Reads the picks made so far off the Yahoo draft-room tab (Claude in Chrome) and writes them to scrape/picks.json for the Streamlit optimizer. Run as `/loop 30s /watch-draft` for the whole draft. Use when the user says "watch the draft", "start the draft watcher", or invokes /watch-draft.
---

# /watch-draft — one tick

The Streamlit app (`streamlit run app.py`, sidebar "Pick source > Draft room > Watch scrape feed"
toggled on, "My team name" set to **Your Team**) polls `scrape/picks.json`. Your job each tick:
read the new picks off the draft room's *Results > Round by Round* table and append them to
the feed. Rehearsed against a live Yahoo mock on 2026-09-04; this recipe is what worked.

## Steps

1. **Find the tab.** If the Chrome tools are not loaded, load them in ONE ToolSearch call:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer`.
   Call `tabs_context_mcp` and pick the tab whose URL is on
   `football.fantasysports.yahoo.com/draftclient/`. Remember its tab id across ticks. Never open a
   new tab or navigate the draft tab: the user is drafting in it.
2. **Read the new picks** with `javascript_tool` (one call; output is capped near 1000 chars, so
   only ask for picks after the last one you wrote — set `MIN`):

   ```js
   const MIN = <last pick written + 1>;
   let t = document.querySelector('table');
   if (!t || !/^Pick/.test(t.rows[0]?.innerText || '')) {           // Results > Round by Round not showing
     [...document.querySelectorAll('button')].find(e => e.textContent.trim() === 'Results')?.click();
     await new Promise(s => setTimeout(s, 800));
     [...document.querySelectorAll('div,span,button')].find(e => e.children.length === 0 && e.textContent.trim() === 'Round by Round')?.click();
     await new Promise(s => setTimeout(s, 800));
     t = document.querySelector('table');
   }
   const out = [];
   for (const r of t.rows) {
     if (r.cells.length < 3) continue;
     const pick = +r.cells[0].innerText.trim(); if (!pick || pick < MIN) continue;
     const pl = r.cells[1], id = pl.querySelector('[data-id]')?.dataset.id || '';
     const lines = pl.innerText.split('\n').map(s => s.trim())
       .filter(s => s && !/^(Q|O|IR|D|SUSP|NA|PUP)$/.test(s) && !/^Bye/.test(s));
     out.push([pick, r.cells[2].innerText.trim(), lines[0] || '', lines[1] || '', lines[2] || '', id].join(' | '));
   }
   out.reverse().join('\n')
   ```

   Each line is `pick | fantasy team | abbreviated name | pos | NFL team | yahoo player id`.
   The user's own team is listed as **Your Team**; names are abbreviated ("J. Gibbs") but the
   Yahoo id resolves the player exactly against `data/players.csv`. Kickers and unknown ids fall
   back to a placeholder pick; that is fine.
   The table is newest-first; the snippet reverses it. If `out` is empty, nothing new happened.
3. **Append to the feed** (repo root as cwd), pasting the lines verbatim:

   ```bash
   .venv/bin/python write_picks.py --append <<'PICKS'
   40 | tyler | B. Irving | RB | TB | 40993
   41 | Gregory | T. McMillan | WR | Car | 41793
   PICKS
   ```

   On the very first tick (or if the feed might be stale from an earlier draft) send *all* picks
   in chunks of ~20 (`MIN` = 1, 21, 41, ...) and drop `--append` for the first chunk so the old
   feed is replaced.
4. **Report** one line: `tick: N picks total (M new), last: <player>`.

## Stop conditions

End the loop (reply with the stop instruction for /loop) when:
- the pick count reaches teams × rounds (180 for the league) or the page says the draft is complete, or
- the draft tab is gone, or
- three consecutive ticks find no `Pick` table even after clicking Results (say so; the user may
  have navigated away).

## Do not

- Do not pick players, click "Draft", or touch the queue. Read only (clicking the Results /
  Round by Round tabs is the one allowed interaction).
- Do not use `get_page_text` on the draft room: it is ~10k tokens per call and abbreviates names.
- Do not guess a pick you cannot read; leave it out and mention it in the tick report.
- Do not edit `scrape/picks.json` by hand; always go through `write_picks.py`.
