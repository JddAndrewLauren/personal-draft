---
name: queue-draft
description: Load the Yahoo draft-room Queue with the optimizer's top 5-6 available players so an expired clock autodrafts the app's choice instead of Yahoo's. Run right after a /watch-draft tick whenever the user's pick is 3 or fewer away, or when the user says "load the queue", "star the recs", or invokes /queue-draft.
---

# /queue-draft — star the optimizer's top picks in Yahoo's Queue

Why: an expired clock autodrafts from the Queue top-down; an empty Queue lets Yahoo pick for you
and, after the first miss, flips the team into **autopick mode**. This skill **writes to the room**
(stars and unstars players); `/watch-draft` stays read-only. Selectors below were verified live in
mock #3 (2026-09-04, `docs/mock-draft-2026-09-04c.md`).

## Steps

1. **Get the targets** (repo root as cwd). Ask for **8** and refill after every `/watch-draft` tick
   (live draft 2026-09-05: the user wants the Queue full at all times; with half the room
   autodrafting 2-4 targets vanish between ticks). Before the draft starts, load the top 14: on a
   back-half slot everything above your pick is gone by the time it arrives. In the last 3-4 rounds,
   when the ranking is mostly bench TEs, filter to upside RB/WR and rookies instead:
   `.venv/bin/python draft_cli.py recs --n 30 --ids | grep -E '\|(RB|WR)$' | head -8`. The ranking is merit-only (no reach-my-pick
   discount, commit 6d311fe), so the top targets are often taken before the pick; Yahoo drafts
   the queue top-down skipping taken players, so depth is what keeps the queue from running
   empty (mock #3 pick 108: all four targets gone, then a Yahoo autopick).

   ```bash
   .venv/bin/python draft_cli.py recs --n 8 --ids
   ```

   One line per rec: `yahoo_id|name|pos`. The id can be blank (some DEFs); the script then finds
   the row by name + position. `no more picks for you` means stop.
2. **Find the tab.** If the Chrome tools are not loaded, load them in ONE ToolSearch call:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer`.
   `tabs_context_mcp`, pick the tab on `football.fantasysports.yahoo.com/draftclient/`. Never open
   a new tab or navigate the draft tab.
3. **Star the targets** with ONE `javascript_tool` call. Fill `TARGETS` from step 1. The script
   refuses to run on the clock (the star controls turn into Draft buttons on your turn) and never
   clicks anything labelled Draft.

   ```js
   const TARGETS = [['42654','Jadarian Price','RB'], ['','Jets','DEF'], ['31896','DK Metcalf','WR']];
   const wait = ms => new Promise(s => setTimeout(s, ms));
   const txt = e => (e?.textContent || '').trim();
   // Title is "N picks until your turn" off the clock and "YOUR TURN, DRAFT NOW" on it.
   const onClock = () => /your turn/i.test(document.title) && !/until your turn/i.test(document.title);
   let stop = onClock() ? 'ON CLOCK - skipped' : null;
   const out = [];
   if (!stop) {
     // 1. Unstar queued players that are no longer targets (queue rows and table rows both carry ys-removequeue).
     const ids = new Set(TARGETS.map(t => t[0]).filter(Boolean));
     for (const q of [...document.querySelectorAll('.ys-removequeue[data-id]')]) {
       if (ids.has(q.dataset.id)) continue;
       q.querySelector('button')?.click(); await wait(400); out.push('unstarred stale: ' + q.dataset.id);
     }
     // 2. Players tab must be showing (watch-draft leaves the room on Results).
     if (!document.querySelector('.ys-addqueue')) {
       [...document.querySelectorAll('button,div,span')].find(e => e.children.length === 0 && txt(e) === 'Players')?.click();
       await wait(800);
     }
     const setSearch = async v => {          // React input: native setter + input event
       const inp = document.querySelector('input[placeholder*="earch" i]'); if (!inp) return false;
       Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inp, v);
       inp.dispatchEvent(new Event('input', { bubbles: true })); await wait(1300); return true;
     };
     const findStar = (id, name, pos) => {
       if (id) { const q = document.querySelector(`.ys-addqueue[data-id="${id}"]`); if (q) return q; }
       const needle = pos === 'DEF' ? name : name.split(' ').slice(-1)[0];
       const tr = [...document.querySelectorAll('table tbody tr')].find(r => r.innerText.includes(needle) && r.innerText.includes(pos));
       return tr?.querySelector('.ys-addqueue') || null;
     };
     // 3. Star in rank order: the Queue is ordered by star time, so a re-starred player goes to the end.
     for (const [id, name, pos] of TARGETS) {
       if (id && document.querySelector(`.ys-removequeue[data-id="${id}"]`)) { out.push(`${name}: already queued`); continue; }
       let q = findStar(id, name, pos);
       if (!q) { await setSearch(pos === 'DEF' ? name : name.split(' ').slice(-1)[0]); q = findStar(id, name, pos); }
       if (!q) {
         const drafted = !!document.querySelector('table tbody tr svg[data-icon="checkmark-circle-filled"]');
         out.push(`${name}: ${drafted ? 'already drafted' : 'not found'}`); continue;
       }
       q.querySelector('button').click(); await wait(400); out.push(`${name}: queued`);
     }
     await setSearch('');
     const panel = [...document.querySelectorAll('div,span,p')].find(e => e.children.length === 0 && /Autodraft will pick from queue/i.test(txt(e)))?.parentElement?.parentElement;
     out.push('queue: ' + [...(panel || document).querySelectorAll('.ys-removequeue[data-id]')].map(q => q.dataset.id).join(',') + ' | ' + document.title.slice(0, 30));
   }
   stop || out.join('\n')
   ```

   Total in-page waits stay well under 40 s (`javascript_tool` times out at 45 s).
4. **Queue order.** The `queue:` line lists ids top-down; it must match the TARGETS order. Because
   the Queue is ordered by star time, a target that was already queued from an earlier tick stays
   ahead of newer, higher-ranked targets. The refill used on 2026-09-05 checks
   `cur.join() !== want.filter(id => cur.includes(id)).join()` and, when it differs, unstars the whole
   queue and re-stars every target in rank order (about 250 ms per click, well inside the 45 s cap);
   otherwise it only appends the missing ones. It also fuses the `/watch-draft` row read into the
   same call so refill and read cost one round trip. The `queue:` readback can lag the DOM by a few
   hundred ms and miss the last entries; screenshot if it matters.
5. **Report** one line: `queue: <#1>, <#2>, ... <#5>` plus any `already drafted` / `not found` notes.
   `ON CLOCK - skipped` means run it again after the pick. If the Autodraft pill in the Queue header
   is filled (checkmark), the team is in autopick mode: click it once to turn autopick off and say
   so.

## Verified DOM (mock #3, 2026-09-04)

- Players table: `table tbody tr`; the star is `div.ys-addqueue[data-id=<yahoo id>] > button >
  svg[data-icon="star-unfilled"]` and flips to `star-filled` once queued; the name cell is
  `div.ys-player[data-id]`. A queued player's table row and its Queue-panel row both become
  `div.ys-removequeue[data-id]` (click its button to unstar).
- On your turn every `ys-addqueue` / `ys-removequeue` control disappears and rows show a
  `Draft` button instead; the Queue panel rows show `Draft` too.
- Search results (`input[placeholder*="earch"]`) include already-drafted players, marked with
  `svg[data-icon="checkmark-circle-filled"]` and no star.
- Queue header: "Autodraft will pick from queue" and an `Autodraft` pill; filled = autopick mode on.
- Only the top ~100 by rank are in the table; DEF/K and late targets need the search box.

## Do not

- Never click anything labelled Draft; never run while the title says YOUR TURN.
- Never use `get_page_text` on the draft room (~10k tokens per call).
- Do not drag queue rows to reorder; unstar + re-star instead.
