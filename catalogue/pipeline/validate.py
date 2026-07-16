#!/usr/bin/env python3
"""Validate a catalogue.json array against the CataloguePromo schema.

Usage:
    python3 validate.py path/to/catalogue.json

Exits 0 with no output on success. Exits 1 and prints one error line per
problem found on failure. Runnable standalone on catalogue/catalogue.json.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

REQUIRED_FIELDS = {
    "id",
    "retailer",
    "productOrBrand",
    "programs",
    "pointsType",
    "offerKind",
    "multiplier",
    "discountText",
    "channel",
    "validFrom",
    "validTo",
    "sourceURL",
    "editorialNote",
    "lastVerified",
}

ALLOWED_PROGRAMS = {
    "everydayRewards",
    "flybuys",
    "qantasFF",
    "velocityFF",
    "onePass",
    "pricelineSisterClub",
    "myerOne",
    "amexMembershipRewards",
}
ALLOWED_POINTS_TYPES = {"storeRewards", "frequentFlyer"}
ALLOWED_OFFER_KINDS = {"multiplier", "discount"}
ALLOWED_CHANNELS = {"online", "inStore", "both", None}


def parse_iso(value, field_name: str, errors: list[str], index: int) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"[{index}] {field_name} must be an ISO-8601 string, got {type(value).__name__}")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"[{index}] {field_name} is not a valid ISO-8601 date: {value!r}")
        return None


def validate_url(url, field_name: str, errors: list[str], index: int) -> None:
    if not isinstance(url, str) or not url:
        errors.append(f"[{index}] {field_name} must be a non-empty string")
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append(f"[{index}] {field_name} is not a well-formed http(s) URL: {url!r}")


def validate_promo(promo: dict, index: int) -> list[str]:
    errors: list[str] = []

    if not isinstance(promo, dict):
        return [f"[{index}] entry is not an object"]

    missing = REQUIRED_FIELDS - promo.keys()
    if missing:
        errors.append(f"[{index}] missing required fields: {sorted(missing)}")
        # Field-level checks below assume presence; bail early on hard-missing id/basic fields.

    promo_id = promo.get("id")
    if not isinstance(promo_id, str) or not promo_id:
        errors.append(f"[{index}] id must be a non-empty string")

    retailer = promo.get("retailer")
    if not isinstance(retailer, str) or not retailer:
        errors.append(f"[{index}] retailer must be a non-empty string")

    product_or_brand = promo.get("productOrBrand")
    if product_or_brand is not None and not isinstance(product_or_brand, str):
        errors.append(f"[{index}] productOrBrand must be a string or null")

    programs = promo.get("programs")
    if not isinstance(programs, list):
        errors.append(f"[{index}] programs must be an array")
    else:
        bad = [p for p in programs if p not in ALLOWED_PROGRAMS]
        if bad:
            errors.append(f"[{index}] unknown program name(s): {bad}")

    points_type = promo.get("pointsType")
    if points_type not in ALLOWED_POINTS_TYPES:
        errors.append(f"[{index}] pointsType must be one of {sorted(ALLOWED_POINTS_TYPES)}, got {points_type!r}")

    offer_kind = promo.get("offerKind")
    if offer_kind not in ALLOWED_OFFER_KINDS:
        errors.append(f"[{index}] offerKind must be one of {sorted(ALLOWED_OFFER_KINDS)}, got {offer_kind!r}")

    multiplier = promo.get("multiplier")
    discount_text = promo.get("discountText")
    if offer_kind == "multiplier":
        if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool):
            errors.append(f"[{index}] offerKind=multiplier requires a numeric multiplier")
    elif offer_kind == "discount":
        if not isinstance(discount_text, str) or not discount_text:
            errors.append(f"[{index}] offerKind=discount requires a non-empty discountText")

    channel = promo.get("channel")
    if channel not in ALLOWED_CHANNELS:
        errors.append(f"[{index}] channel must be one of {sorted(c for c in ALLOWED_CHANNELS if c)} or null, got {channel!r}")

    valid_from = parse_iso(promo.get("validFrom"), "validFrom", errors, index) if "validFrom" in promo else None
    valid_to = parse_iso(promo.get("validTo"), "validTo", errors, index) if "validTo" in promo else None
    if "lastVerified" in promo:
        parse_iso(promo.get("lastVerified"), "lastVerified", errors, index)

    if valid_from and valid_to and valid_to <= valid_from:
        errors.append(f"[{index}] validTo ({valid_to.isoformat()}) must be after validFrom ({valid_from.isoformat()})")

    if "sourceURL" in promo:
        validate_url(promo.get("sourceURL"), "sourceURL", errors, index)

    editorial_note = promo.get("editorialNote")
    if editorial_note is not None and not isinstance(editorial_note, str):
        errors.append(f"[{index}] editorialNote must be a string or null")

    return errors


def validate_catalogue(promos) -> list[str]:
    errors: list[str] = []

    if not isinstance(promos, list):
        return ["catalogue.json root must be a JSON array"]

    ids = []
    for i, promo in enumerate(promos):
        errors.extend(validate_promo(promo, i))
        if isinstance(promo, dict) and isinstance(promo.get("id"), str):
            ids.append(promo["id"])

    duplicate_ids = {i for i in ids if ids.count(i) > 1}
    if duplicate_ids:
        errors.append(f"duplicate id(s) found: {sorted(duplicate_ids)}")

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate.py path/to/catalogue.json", file=sys.stderr)
        return 2

    path = sys.argv[1]
    try:
        with open(path) as f:
            promos = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read/parse {path}: {exc}", file=sys.stderr)
        return 1

    errors = validate_catalogue(promos)
    if errors:
        print(f"catalogue validation FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"catalogue validation passed ({len(promos)} promos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
