#!/usr/bin/env python3
"""Render a markdown review table for candidate promos, for the PR body.

Usage:
    python3 render_pr.py candidates.json > pr_body.md
    python3 dedupe_merge.py ... | python3 render_pr.py -
"""
from __future__ import annotations

import json
import sys


def escape_md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def format_offer(promo: dict) -> str:
    if promo.get("offerKind") == "multiplier" and promo.get("multiplier") is not None:
        return f"{promo['multiplier']:g}x"
    return promo.get("discountText") or "—"


def format_programs(promo: dict) -> str:
    programs = promo.get("programs") or []
    return ", ".join(programs) if programs else "—"


def render_table(promos: list[dict]) -> str:
    if not promos:
        return "No new candidate promos this week."

    lines = [
        "| Retailer | Offer | Programs | Valid to | Source | Confidence |",
        "|---|---|---|---|---|---|",
    ]
    for promo in promos:
        retailer = escape_md(promo.get("retailer", ""))
        offer = escape_md(format_offer(promo))
        programs = escape_md(format_programs(promo))
        valid_to = escape_md(promo.get("validTo", ""))
        source = promo.get("sourceURL", "")
        source_cell = f"[link]({source})" if source else "—"
        confidence = promo.get("_confidence")
        confidence_cell = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "—"
        lines.append(f"| {retailer} | {offer} | {programs} | {valid_to} | {source_cell} | {confidence_cell} |")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_pr.py path/to/candidates.json (or '-' for stdin)", file=sys.stderr)
        return 2

    path = sys.argv[1]
    if path == "-":
        promos = json.load(sys.stdin)
    else:
        with open(path) as f:
            promos = json.load(f)

    print(render_table(promos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
