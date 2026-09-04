"""Merge a FantasyPros consensus-rankings export into the master player table.

The export (``RK, TIERS, PLAYER NAME, TEAM, POS, BYE WEEK, BEST, WORST, AVG., STD.DEV, ECR VS. ADP``)
is what ``data/fantasypros_ecr_std.csv`` holds (grabbed from the site's ``ecrData`` via Claude in
Chrome).  This writes ``RankAvg``, ``RankBest``, ``RankWorst``, ``RankStdDev`` (the tool's risk
labels and rank-based value use these) plus an informational ``FPTier`` into ``data/players.csv``.
``ExternalTier`` is left to the DraftSheets tiers (see draftsheets.py).

Usage::

    python fantasypros_ecr.py data/fantasypros_ecr_std.csv
"""
from __future__ import annotations

import argparse
import sys

from models import enrich_key, enrich_players_csv, load_config, read_external

COLUMNS = ["RankAvg", "RankBest", "RankWorst", "RankStdDev", "FPTier"]


def merge(ecr_csv, players_csv) -> dict:
    rows = read_external(ecr_csv)
    updates = {}
    for r in rows:
        updates[enrich_key(r["name"], r["position"], r["team"])] = {
            "RankAvg": r["rank_avg"], "RankBest": r["rank_best"], "RankWorst": r["rank_worst"],
            "RankStdDev": r["rank_stddev"], "FPTier": r["tier"], "_team": r["team"], "_row": r,
        }
    res = enrich_players_csv(players_csv, updates, COLUMNS)
    return {"rows": len(rows), "matched": res["matched"],
            "unmatched": [updates[k]["_row"] for k in res["unmatched_keys"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ecr_csv", nargs="?", default="data/fantasypros_ecr_std.csv")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--players", default=None)
    a = ap.parse_args(argv)
    players_csv = a.players or load_config(a.config)["paths"]["players_csv"]
    res = merge(a.ecr_csv, players_csv)
    top = [f"{r['name']} ({r['position']}, rk {int(r['rank'])})" for r in res["unmatched"] if r["rank"] and r["rank"] <= 200]
    print(f"{res['rows']} ECR rows; merged {res['matched']} into {players_csv}; unmatched in top 200: {top or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
