#!/usr/bin/env python3
"""Dedupe normalized candidates against the current catalogue and emit new ones.

Usage:
    python3 dedupe_merge.py --catalogue catalogue.json --candidates normalized.json

Drops:
  - candidates that duplicate a live catalogue promo (same retailer, similar
    offer, overlapping validity window)
  - candidates that are already expired (validTo < now)

Assigns stable ids (already assigned by normalize.py, but re-checked/uniqued
here against the live catalogue) and prints the list of NEW promos (JSON) to
add, on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_retailer(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def offer_signature(promo: dict) -> tuple:
    offer_kind = promo.get("offerKind")
    if offer_kind == "multiplier":
        value = promo.get("multiplier")
    else:
        value = (promo.get("discountText") or "").strip().lower()
    return (offer_kind, value)


def windows_overlap(a: dict, b: dict) -> bool:
    try:
        a_from, a_to = parse_dt(a["validFrom"]), parse_dt(a["validTo"])
        b_from, b_to = parse_dt(b["validFrom"]), parse_dt(b["validTo"])
    except (KeyError, ValueError):
        return False
    return a_from <= b_to and b_from <= a_to


def is_duplicate(candidate: dict, live_promos: list[dict]) -> bool:
    cand_retailer = normalize_retailer(candidate.get("retailer", ""))
    cand_sig = offer_signature(candidate)
    for live in live_promos:
        if normalize_retailer(live.get("retailer", "")) != cand_retailer:
            continue
        if offer_signature(live) != cand_sig:
            continue
        if windows_overlap(candidate, live):
            return True
    return False


def is_expired(promo: dict, now: datetime) -> bool:
    try:
        return parse_dt(promo["validTo"]) < now
    except (KeyError, ValueError):
        return True  # malformed/missing -> treat as unusable, drop


def dedupe_within_candidates(candidates: list[dict]) -> list[dict]:
    """Drop later duplicates within the candidate batch itself (same feed hit twice)."""
    kept: list[dict] = []
    for c in candidates:
        if not is_duplicate(c, kept):
            kept.append(c)
    return kept


def ensure_unique_ids(candidates: list[dict], existing_ids: set[str]) -> list[dict]:
    seen = set(existing_ids)
    for c in candidates:
        base_id = c["id"]
        candidate_id = base_id
        suffix = 2
        while candidate_id in seen:
            candidate_id = f"{base_id}-{suffix}"
            suffix += 1
        c["id"] = candidate_id
        seen.add(candidate_id)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", required=True, help="Path to the current live catalogue.json")
    parser.add_argument("--candidates", required=True, help="Path to normalized candidates JSON")
    args = parser.parse_args()

    with open(args.catalogue) as f:
        live_promos = json.load(f)
    with open(args.candidates) as f:
        candidates = json.load(f)

    now = datetime.now(timezone.utc)

    # Drop expired candidates first.
    candidates = [c for c in candidates if not is_expired(c, now)]

    # Drop candidates duplicating a live (non-expired) promo.
    live_active = [p for p in live_promos if not is_expired(p, now)]
    candidates = [c for c in candidates if not is_duplicate(c, live_active)]

    # Drop duplicates within this batch.
    candidates = dedupe_within_candidates(candidates)

    # Strip internal-only fields (e.g. _confidence) before assigning final ids,
    # but keep confidence available for the PR renderer via a separate pass.
    existing_ids = {p["id"] for p in live_promos if "id" in p}
    candidates = ensure_unique_ids(candidates, existing_ids)

    json.dump(candidates, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
