#!/usr/bin/env python3
"""Normalize raw OzBargain candidates into CataloguePromo-shaped JSON via Claude.

Usage:
    python3 fetch.py --feed-file sample.xml | python3 normalize.py > normalized.json
    python3 normalize.py --mock-response-file mock.json < raw_candidates.json

Real runs read ANTHROPIC_API_KEY from the environment and call the Anthropic
Messages API (model claude-sonnet-5) once per candidate. Tests / dry runs pass
--mock-response-file (a JSON file containing one canned LLM response object,
or a list of response objects consumed in order) so no network call and no
API key are required.
"""
from __future__ import annotations

import argparse
import json
import sys

MODEL = "claude-sonnet-5"
CONFIDENCE_THRESHOLD = 0.6

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

SYSTEM_PROMPT = """You extract Australian points/loyalty and gift-card deals from OzBargain \
listings into a strict JSON schema for a personal deal-tracking app.

Only extract deals that are genuinely about earning or redeeming LOYALTY/FREQUENT-FLYER \
POINTS, or a store-specific gift-card multiplier/bonus. If the deal is a generic \
percentage-off, cashback-only (e.g. ShopBack/TopCashback with no points angle), or \
otherwise not a points/loyalty deal, return {"skip": true, "reason": "<short reason>"}.

When it IS a points/loyalty deal, return a single JSON object with EXACTLY these fields:
{
  "retailer": string,                 // the retailer/brand name, e.g. "Coles"
  "productOrBrand": string | null,     // what's being purchased, if distinct from retailer
  "programs": string[],                // subset of: everydayRewards, flybuys, qantasFF,
                                        // velocityFF, onePass, pricelineSisterClub, myerOne,
                                        // amexMembershipRewards. Map loosely-named programs
                                        // (e.g. "Qantas Points" -> qantasFF, "Flybuys" -> flybuys).
                                        // Empty array if no program is identifiable.
  "pointsType": "storeRewards" | "frequentFlyer",
  "offerKind": "multiplier" | "discount",
  "multiplier": number | null,         // e.g. 20.0 for "20x points" — required if offerKind is multiplier
  "discountText": string | null,       // e.g. "$50 back" — required if offerKind is discount
  "channel": "online" | "inStore" | "both" | null,
  "editorialNote": string | null,      // one short curator sentence, or null
  "confidence": number                 // 0-1, your confidence this extraction is accurate
}

Use the provided expiry as-is; do not invent a date. If required fields cannot be \
determined confidently, lower the confidence score rather than guessing.
Respond with ONLY the JSON object, no other text."""


def build_user_prompt(candidate: dict) -> str:
    return (
        f"Title: {candidate.get('title', '')}\n"
        f"Description: {candidate.get('description', '')}\n"
        f"Tags: {', '.join(candidate.get('tags', []))}\n"
        f"Expiry (use verbatim as validTo if present): {candidate.get('expiry') or 'unknown'}\n"
        f"Source URL: {candidate.get('sourceURL', '')}\n"
    )


def call_llm_real(candidate: dict) -> dict:
    import anthropic  # imported lazily so tests never require the package

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(candidate)}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


class MockLLM:
    """Feeds canned responses in order; falls back to a single repeated response."""

    def __init__(self, responses: list[dict]):
        self._responses = responses
        self._index = 0

    def __call__(self, candidate: dict) -> dict:
        if not self._responses:
            return {"skip": True, "reason": "no mock response configured"}
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp
        # Repeat the last response if we run out (keeps tests simple).
        return self._responses[-1]


def load_mock_responses(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return [data]


def make_promo_id(retailer: str, source_url: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{retailer}|{source_url}".encode()).hexdigest()[:10]  # noqa: S324
    slug = "".join(c.lower() if c.isalnum() else "-" for c in retailer).strip("-") or "promo"
    return f"ozb-{slug}-{digest}"


def validate_llm_fields(fields: dict) -> str | None:
    """Return an error string if `fields` doesn't map to a valid promo, else None."""
    if not fields.get("retailer"):
        return "missing retailer"
    programs = fields.get("programs", [])
    if not isinstance(programs, list) or any(p not in ALLOWED_PROGRAMS for p in programs):
        return "invalid programs"
    if fields.get("pointsType") not in ALLOWED_POINTS_TYPES:
        return "invalid pointsType"
    offer_kind = fields.get("offerKind")
    if offer_kind not in ALLOWED_OFFER_KINDS:
        return "invalid offerKind"
    if offer_kind == "multiplier" and not isinstance(fields.get("multiplier"), (int, float)):
        return "multiplier offerKind missing numeric multiplier"
    if offer_kind == "discount" and not fields.get("discountText"):
        return "discount offerKind missing discountText"
    if fields.get("channel") not in ALLOWED_CHANNELS:
        return "invalid channel"
    return None


def normalize_candidate(candidate: dict, llm_call) -> dict | None:
    try:
        fields = llm_call(candidate)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: LLM call failed for {candidate.get('title')}: {exc}", file=sys.stderr)
        return None

    if fields.get("skip"):
        return None

    confidence = fields.get("confidence", 0)
    if not isinstance(confidence, (int, float)) or confidence < CONFIDENCE_THRESHOLD:
        return None

    error = validate_llm_fields(fields)
    if error:
        print(f"warning: dropping un-mappable item ({error}): {candidate.get('title')}", file=sys.stderr)
        return None

    valid_to = candidate.get("expiry")
    if not valid_to:
        # No expiry from OzBargain meta — can't safely include without a validTo.
        return None
    valid_from = candidate.get("pubDate") or valid_to

    retailer = fields["retailer"]
    source_url = candidate.get("sourceURL") or candidate.get("link", "")

    return {
        "id": make_promo_id(retailer, source_url),
        "retailer": retailer,
        "productOrBrand": fields.get("productOrBrand"),
        "programs": fields.get("programs", []),
        "pointsType": fields["pointsType"],
        "offerKind": fields["offerKind"],
        "multiplier": fields.get("multiplier"),
        "discountText": fields.get("discountText"),
        "channel": fields.get("channel"),
        "validFrom": valid_from,
        "validTo": valid_to,
        "sourceURL": source_url,
        "editorialNote": fields.get("editorialNote"),
        "lastVerified": candidate.get("pubDate") or valid_to,
        "_confidence": confidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-response-file", default=None, help="JSON file with canned LLM response(s); skips the real API call.")
    parser.add_argument("--mock", action="store_true", help="Alias flag: use an empty mock (all items skipped). Mostly for smoke tests.")
    args = parser.parse_args()

    candidates = json.load(sys.stdin)

    if args.mock_response_file:
        mock = MockLLM(load_mock_responses(args.mock_response_file))
        llm_call = mock
    elif args.mock:
        llm_call = MockLLM([])
    else:
        llm_call = call_llm_real

    normalized = []
    for candidate in candidates:
        result = normalize_candidate(candidate, llm_call)
        if result is not None:
            normalized.append(result)

    json.dump(normalized, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
