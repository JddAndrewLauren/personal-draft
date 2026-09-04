---
name: queue-draft
description: Load the Yahoo draft-room Queue with the optimizer's top 2-3 available players so an expired clock autodrafts the app's choice instead of Yahoo's. Run right after a /watch-draft tick whenever the user's pick is 3 or fewer away, or when the user says "load the queue", "star the recs", or invokes /queue-draft.
---

# /queue-draft — star the optimizer's top picks in Yahoo's Queue

Why: in mock #2 two expired clocks put the team into Yahoo **autopick mode** and the room finished
the draft in about a minute. Yahoo's autodraft takes from the Queue in order, so a queue that
always holds the app's top 2-3 turns a missed clock into the app's own pick. This skill **writes
to the room** (stars players); `/watch-draft` stays read-only.

## Steps

1. **Get the targets** (repo root as cwd):

   ```bash
   .venv/bin/python draft_cli.py recs --n 3 --ids
   ```

   One line per rec: `yahoo_id|name|pos` (the id can be blank for players without a Yahoo id;
   fall back to searching by name for those). `no more picks for you` means stop.
2. **Find the tab.** If the Chrome tools are not loaded, load them in ONE ToolSearch call:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer`.
   `tabs_context_mcp`, pick the tab on `football.fantasysports.yahoo.com/draftclient/`. Never open
   a new tab or navigate the draft tab.
3. **Star the targets** with ONE `javascript_tool` call. Fill `TARGETS` from step 1. The script
   refuses to run while on the clock (the row's star turns into a Draft button on your turn;
   clicking it drafted the player at pick 22 in mock #2) and never clicks anything labelled Draft.

   ```js
   const TARGETS = [['30123','Bijan Robinson','RB'], ['33466','Puka Nacua','WR'], ['100034','Texans','DEF']];
   const wait = ms => new Promise(s => setTimeout(s, ms));
   const txt = e => (e?.textContent || '').trim();
   const isDraft = e => /draft/i.test(txt(e)) || /draft/i.test(e?.getAttribute?.('aria-label') || '') || /draft/i.test(e?.getAttribute?.('title') || '');
   let stop = /your turn/i.test(document.title) ? 'ON CLOCK - skipped' : null;
   const CTRL = 'button,[role=button],[role=checkbox],svg,a';
   const rowOf = el => el?.closest('tr') || el?.closest('[role=row]') || el?.closest('li');
   const starOf = row => [...row.querySelectorAll(CTRL)].find(c => !isDraft(c) && !c.closest('[data-id]')); // first control that is not the name link and not Draft
   const queued = row => { const s = starOf(row); return !!(s && (s.getAttribute('aria-pressed') === 'true' || s.getAttribute('aria-checked') === 'true' || /active|selected|queued|filled/i.test(s.className?.baseVal ?? s.className ?? ''))); };
   const setSearch = async v => {          // React input: native setter + input event
     const inp = document.querySelector('input[type=search],input[placeholder*="earch" i],input[type=text]');
     if (!inp) return false;
     Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inp, v);
     inp.dispatchEvent(new Event('input', { bubbles: true }));
     await wait(1200);
     return true;
   };
   // Players tab must be showing (watch-draft leaves the room on Results).
   if (!stop && (!document.querySelector('[data-id]') || /^Pick/.test(document.querySelector('table')?.rows[0]?.innerText || ''))) {
     [...document.querySelectorAll('button,div,span')].find(e => e.children.length === 0 && txt(e) === 'Players')?.click();
     await wait(800);
   }
   if (!stop && [...document.querySelectorAll('tr,[role=row]')].some(r => isDraft(starOf(r)))) stop = 'ON CLOCK - skipped (Draft controls visible)';
   const out = [];
   for (const [id, name, pos] of (stop ? [] : TARGETS)) {
     let row = id ? rowOf(document.querySelector(`[data-id="${id}"]`)) : null;
     if (!row) {                                             // outside the top ~100: search
       const needle = pos === 'DEF' ? name : name.split(' ').slice(-1)[0];
       if (await setSearch(needle)) {
         row = id ? rowOf(document.querySelector(`[data-id="${id}"]`)) : null;
         if (!row) row = [...document.querySelectorAll('tr,[role=row]')].find(r => r.innerText.includes(name.split(' ').slice(-1)[0]) && r.innerText.includes(pos));
       }
     }
     if (!row) { out.push(`${name}: not found`); continue; }
     const star = starOf(row);
     if (!star) { out.push(`${name}: no star control`); continue; }
     if (isDraft(star)) { out.push(`${name}: skipped (Draft control)`); continue; }
     if (queued(row)) { out.push(`${name}: already queued`); continue; }
     star.click(); await wait(400);
     out.push(`${name}: queued`);
   }
   if (!stop) await setSearch('');
   const q = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && /^Queue/i.test(txt(e)))?.closest('section,div');
   const qn = q ? [...q.querySelectorAll('[data-id]')].map(e => txt(e)).filter(Boolean) : [];
   out.push('queue panel: ' + (qn.join(', ') || 'unreadable'));
   stop || out.join('\n')
   ```

   Total in-page waits stay well under 40 s (`javascript_tool` times out at 45 s).
4. **Queue order.** Autodraft takes the queue top-down. If the `queue panel:` line lists a
   player ahead of the targets who is no longer in the top 3, report `stale ahead: <name>`. Remove
   it only if the Queue panel shows an obvious remove/unstar control for that row; otherwise just
   report it. Do not spend a second tool call hunting for it.
5. **Report** one line: `queue: <#1>, <#2>, <#3>` plus any `not found` / `no star control` /
   `stale ahead` notes. `ON CLOCK - skipped` means run it again after the pick.

## Selectors are provisional

Verified live so far: rows carry `data-id` = Yahoo player id; the star is the row's first control
before the user's turn; the search box is a React input; the Draft control in a search-result row
is not a `<button>`. The star and queue-panel selectors above are best guesses. On the first mock
run, start with a read-only probe and pin the real selectors into the script above:

```js
const r = document.querySelector('[data-id]')?.closest('tr,[role=row]');
(r?.outerHTML || 'no row').slice(0, 900)
```

Then the same for a queued row and the Queue panel. If the star control cannot be found on two
consecutive runs, stop and say so instead of clicking guesses.

## Do not

- Never click anything labelled Draft; never run while the title says YOUR TURN.
- Never use `get_page_text` on the draft room (~10k tokens per call).
- Do not remove queue entries you cannot positively identify.
