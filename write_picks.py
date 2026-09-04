#!/usr/bin/env python3
"""Write the draft-room scrape feed from a compact text listing on stdin.

    python write_picks.py [--path scrape/picks.json] < picks.txt

One pick per line, pipe-separated:  pick | team name | player | position | NFL team | yahoo id
The last three fields are optional. Blank lines and lines starting with '#' are ignored.
The file is a full snapshot: send every pick made so far, every time -- or pass --append to
merge the lines on stdin into the existing feed (keyed by pick number), so a tick only has
to carry the picks made since the last one.
"""
from __future__ import annotations

import argparse
import sys

from scrape import load_picks, write_picks


def parse_lines(text: str) -> list:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"need at least 'pick | team | player': {line!r}")
        parts += [""] * (6 - len(parts))
        rows.append({"pick": int(parts[0]), "team": parts[1], "player": parts[2],
                     "position": parts[3], "nfl_team": parts[4], "yahoo_id": parts[5]})
    rows.sort(key=lambda r: r["pick"])
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="scrape/picks.json")
    ap.add_argument("--append", action="store_true", help="merge into the existing feed instead of replacing it")
    a = ap.parse_args(argv)
    rows = parse_lines(sys.stdin.read())
    if a.append:
        merged = {r["pick"]: r for r in load_picks(a.path)["picks"]}
        merged.update({r["pick"]: r for r in rows})
        rows = [merged[k] for k in sorted(merged)]
    write_picks(rows, a.path)
    print(f"wrote {len(rows)} picks -> {a.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
