#!/usr/bin/env python3
"""Write the draft-room scrape feed from a compact text listing on stdin.

    python write_picks.py [--path scrape/picks.json] < picks.txt

One pick per line, pipe-separated:  pick | team name | player | position | NFL team
The last two fields are optional. Blank lines and lines starting with '#' are ignored.
The file is a full snapshot: send every pick made so far, every time.
"""
from __future__ import annotations

import argparse
import sys

from scrape import write_picks


def parse_lines(text: str) -> list:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"need at least 'pick | team | player': {line!r}")
        parts += [""] * (5 - len(parts))
        rows.append({"pick": int(parts[0]), "team": parts[1], "player": parts[2],
                     "position": parts[3], "nfl_team": parts[4]})
    rows.sort(key=lambda r: r["pick"])
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="scrape/picks.json")
    a = ap.parse_args(argv)
    rows = parse_lines(sys.stdin.read())
    write_picks(rows, a.path)
    print(f"wrote {len(rows)} picks -> {a.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
