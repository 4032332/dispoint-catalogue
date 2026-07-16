#!/usr/bin/env python3
"""Fetch OzBargain tag feeds and emit raw candidate promos as JSON on stdout.

Usage:
    python3 fetch.py [--feed-file PATH ...] [--min-votes N]

If no --feed-file is given, fetches the default set of OzBargain tag feeds over
the network. --feed-file may be passed multiple times to parse local files
instead (used by tests, avoids any network access).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

try:
    # Untrusted network XML — prefer defusedxml (guards against XXE / entity
    # expansion bombs) when available.
    import defusedxml.ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only when dep missing
    import xml.etree.ElementTree as ET  # noqa: S405

    print(
        "warning: defusedxml not installed — parsing feed XML with the "
        "stdlib parser, which is not hardened against XXE/entity-expansion "
        "attacks. Run `pip install defusedxml` for safer parsing.",
        file=sys.stderr,
    )

OZB_NS = "https://www.ozbargain.com.au"

DEFAULT_FEEDS = [
    "https://www.ozbargain.com.au/tag/gift-card/feed",
    "https://www.ozbargain.com.au/tag/frequent-flyer/feed",
    "https://www.ozbargain.com.au/tag/flybuys/feed",
    "https://www.ozbargain.com.au/tag/velocity/feed",
    "https://www.ozbargain.com.au/tag/qantas/feed",
    "https://www.ozbargain.com.au/tag/everyday-rewards/feed",
    # General recent-deals feed: the source of current discount-CODE / voucher deals.
    # (OzBargain's dedicated coupon/discount-code tags are dead — only old, expired
    # nodes — so we pull the front-page feed and let normalize.py keep only genuine
    # coupon/voucher offers and drop one-off product price-drops.)
    "https://www.ozbargain.com.au/deals/feed",
]

DEFAULT_VOTE_THRESHOLD = 15
USER_AGENT = "DisPointCataloguePipeline/1.0 (+https://github.com/)"


def strip_html(text: str) -> str:
    """Very small HTML-to-text helper: strip tags, collapse whitespace."""
    import re

    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#039;", "'", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_feed_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read()


def parse_feed(xml_bytes: bytes, source_feed: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)  # noqa: S314
    channel = root.find("channel")
    if channel is None:
        return []

    candidates = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description_raw = item.findtext("description") or ""
        description = strip_html(description_raw)

        tags = [
            (cat.text or "").strip()
            for cat in item.findall("category")
            if (cat.text or "").strip()
        ]

        pub_date_raw = (item.findtext("pubDate") or "").strip()
        pub_date_iso = None
        if pub_date_raw:
            try:
                pub_date_iso = parsedate_to_datetime(pub_date_raw).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                pub_date_iso = None

        meta = item.find(f"{{{OZB_NS}}}meta")
        expiry_raw = meta.get("expiry") if meta is not None else None
        votes_pos = int(meta.get("votes-pos", "0")) if meta is not None else 0
        votes_neg = int(meta.get("votes-neg", "0")) if meta is not None else 0
        real_url = meta.get("url") if meta is not None else None

        expiry_iso = None
        if expiry_raw:
            try:
                expiry_iso = datetime.fromisoformat(expiry_raw).astimezone(timezone.utc).isoformat()
            except ValueError:
                expiry_iso = None

        candidates.append(
            {
                "title": title,
                "description": description,
                "link": link,
                "sourceURL": real_url or link,
                "tags": tags,
                "pubDate": pub_date_iso,
                "expiry": expiry_iso,
                "votesPos": votes_pos,
                "votesNeg": votes_neg,
                "sourceFeed": source_feed,
            }
        )
    return candidates


def is_expired(candidate: dict, now: datetime) -> bool:
    expiry = candidate.get("expiry")
    if not expiry:
        return False
    try:
        expiry_dt = datetime.fromisoformat(expiry)
    except ValueError:
        return False
    return expiry_dt < now


def filter_candidates(candidates: list[dict], min_votes: int, now: datetime) -> list[dict]:
    filtered = []
    for c in candidates:
        score = c["votesPos"] - c["votesNeg"]
        if score < min_votes:
            continue
        if is_expired(c, now):
            continue
        filtered.append(c)
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed-file", action="append", default=None, help="Parse a local feed XML file instead of fetching over the network. May be repeated.")
    parser.add_argument("--feed-url", action="append", default=None, help="Override the default feed URL list. May be repeated.")
    parser.add_argument("--min-votes", type=int, default=DEFAULT_VOTE_THRESHOLD)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    all_candidates: list[dict] = []

    if args.feed_file:
        for path in args.feed_file:
            with open(path, "rb") as f:
                xml_bytes = f.read()
            all_candidates.extend(parse_feed(xml_bytes, source_feed=path))
    else:
        feeds = args.feed_url or DEFAULT_FEEDS
        for url in feeds:
            try:
                xml_bytes = fetch_feed_bytes(url)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: failed to fetch {url}: {exc}", file=sys.stderr)
                continue
            all_candidates.extend(parse_feed(xml_bytes, source_feed=url))

    filtered = filter_candidates(all_candidates, args.min_votes, now)
    json.dump(filtered, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
