#!/usr/bin/env python3
"""Roll the live catalogue forward: drop expired promos, fold in this week's new
candidates, and cap the result at a fixed target size so the catalogue is a small,
always-current, admin-reviewed window rather than an ever-growing pile.

Usage:
    python3 roll_catalogue.py --catalogue catalogue.json --candidates new.json \
        [--target 10] [--out catalogue.json]

Selection policy (why these survive):
  1. Never keep an expired promo (validTo < now) — those auto-vanish.
  2. This week's freshly-reviewed NEW candidates take priority (they're the point of
     the weekly run), ranked by confidence then longest validity.
  3. Remaining slots are filled with still-valid promos already in the catalogue,
     ranked by longest remaining validity (the most durable, safest-to-show ones).
  4. Truncate to --target (default 10).

Writes the rolled catalogue (internal _-prefixed fields stripped) to --out, or to
stdout if --out is omitted. Prints a one-line summary to stderr.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

DEFAULT_TARGET = 10


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(promo: dict, now: datetime) -> bool:
    valid_to = promo.get("validTo")
    if not valid_to:
        return False
    try:
        return parse_dt(valid_to) < now
    except (ValueError, TypeError):
        # Unparseable date — treat as expired so it can't linger.
        return True


def _valid_to_key(promo: dict) -> datetime:
    try:
        return parse_dt(promo.get("validTo"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _confidence(promo: dict) -> float:
    c = promo.get("_confidence")
    return float(c) if isinstance(c, (int, float)) else 0.0


def strip_internal(promo: dict) -> dict:
    return {k: v for k, v in promo.items() if not k.startswith("_")}


def roll(live: list[dict], new: list[dict], now: datetime, target: int = DEFAULT_TARGET) -> list[dict]:
    """Return the rolled window (fresh new candidates first, then durable survivors),
    capped at `target`, with expired promos and duplicate ids removed. Each returned
    promo carries an internal `_isNew` flag; internal `_`-fields are NOT stripped here
    (main() strips them for the published catalogue). Use `strip_internal` for output."""
    new_valid = [dict(p, _isNew=True) for p in new if not is_expired(p, now)]
    # New candidates: best confidence first, then longest-lived.
    new_valid.sort(key=lambda p: (_confidence(p), _valid_to_key(p)), reverse=True)

    seen_ids = {p.get("id") for p in new_valid}
    survivors = [
        dict(p, _isNew=False) for p in live
        if not is_expired(p, now) and p.get("id") not in seen_ids
    ]
    # Survivors: keep the ones that stay valid the longest.
    survivors.sort(key=_valid_to_key, reverse=True)

    return (new_valid + survivors)[:target]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", required=True, help="Path to the live catalogue.json.")
    parser.add_argument("--candidates", required=True, help="Path to this week's new candidates JSON.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Max promos to keep (default 10).")
    parser.add_argument("--out", default=None, help="Write the published (clean) catalogue here instead of stdout.")
    parser.add_argument("--render-out", default=None, help="Also write the rolled list WITH internal fields (_confidence, _isNew) here, for the PR renderer.")
    args = parser.parse_args()

    with open(args.catalogue) as f:
        live = json.load(f)
    with open(args.candidates) as f:
        new = json.load(f)

    rolled = roll(live, new, datetime.now(timezone.utc), args.target)

    if args.render_out:
        with open(args.render_out, "w") as f:
            f.write(json.dumps(rolled, indent=2) + "\n")

    published = [strip_internal(p) for p in rolled]
    text = json.dumps(published, indent=2) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)

    rolled_ids = {p.get("id") for p in rolled}
    dropped = sum(1 for p in live if p.get("id") not in rolled_ids)
    print(f"rolled catalogue: {len(published)} promos (was {len(live)}; {dropped} dropped/expired)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
