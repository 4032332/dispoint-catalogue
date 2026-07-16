#!/usr/bin/env python3
"""Tests for the catalogue sourcing pipeline. No network access, no API key.

Run with:
    python3 catalogue/tests/test_pipeline.py
or:
    python3 -m pytest catalogue/tests/ -q   (if pytest is installed)
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOGUE_DIR = os.path.dirname(TESTS_DIR)
PIPELINE_DIR = os.path.join(CATALOGUE_DIR, "pipeline")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
SAMPLE_FEED = os.path.join(TESTS_DIR, "sample_ozbargain_feed.xml")
LIVE_CATALOGUE = os.path.join(CATALOGUE_DIR, "catalogue.json")

sys.path.insert(0, PIPELINE_DIR)

import fetch  # noqa: E402
import normalize  # noqa: E402
import dedupe_merge  # noqa: E402
import render_pr  # noqa: E402
import roll_catalogue  # noqa: E402
import validate  # noqa: E402


class FetchTests(unittest.TestCase):
    def test_parses_sample_feed_into_candidates(self):
        with open(SAMPLE_FEED, "rb") as f:
            xml_bytes = f.read()
        candidates = fetch.parse_feed(xml_bytes, source_feed=SAMPLE_FEED)

        # 10 <item> elements in the sample feed.
        self.assertEqual(len(candidates), 10)

        first = candidates[0]
        self.assertIn("TopCashback", first["title"])
        self.assertEqual(first["votesPos"], 367)
        self.assertEqual(first["votesNeg"], 0)
        self.assertEqual(
            first["sourceURL"],
            "https://www.topcashback.com.au/bonus/july26-giftcard-optin-mx",
        )
        self.assertEqual(first["expiry"], "2026-07-16T13:59:00+00:00")
        self.assertTrue(first["pubDate"])
        # description HTML should be stripped to plain text
        self.assertNotIn("<", first["description"])

    def test_item_without_expiry_meta_has_none_expiry(self):
        with open(SAMPLE_FEED, "rb") as f:
            xml_bytes = f.read()
        candidates = fetch.parse_feed(xml_bytes, source_feed=SAMPLE_FEED)
        costco_auto_renewal = next(c for c in candidates if "Auto Renewal" in c["title"])
        self.assertIsNone(costco_auto_renewal["expiry"])

    def test_filters_low_vote_and_expired_items(self):
        with open(SAMPLE_FEED, "rb") as f:
            xml_bytes = f.read()
        candidates = fetch.parse_feed(xml_bytes, source_feed=SAMPLE_FEED)

        # Fix "now" so this test doesn't depend on wall-clock time.
        now = datetime(2026, 7, 16, 1, 18, tzinfo=timezone.utc)  # matches system date used in dev
        filtered = fetch.filter_candidates(candidates, min_votes=15, now=now)

        titles = [c["title"] for c in filtered]

        # Low-vote items dropped (Myer $10: 7 votes; Activ Visa: 1-5=-4 votes)
        self.assertFalse(any("MYER" in t and "Click" in t for t in titles))
        self.assertFalse(any("Activ Visa" in t for t in titles))

        # Expired item dropped (ShopBack Super Swap expires 2026-07-13, before `now`)
        self.assertFalse(any("Super Swap" in t for t in titles))

        # High-vote, non-expired items kept
        self.assertTrue(any("TopCashback" in t for t in titles))
        self.assertTrue(any("Auto Renewal" in t for t in titles))  # no expiry -> never filtered by expiry

    def test_cli_feed_file_arg_produces_json_on_stdout(self):
        buf = io.StringIO()
        argv_backup = sys.argv
        try:
            sys.argv = ["fetch.py", "--feed-file", SAMPLE_FEED, "--min-votes", "15"]
            with redirect_stdout(buf):
                fetch.main()
        finally:
            sys.argv = argv_backup

        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)


class NormalizeTests(unittest.TestCase):
    def test_mocked_llm_produces_valid_schema_output(self):
        candidates = [
            {
                "title": "20x Flybuys points on gift cards @ Coles",
                "description": "Bonus points on selected gift cards.",
                "link": "https://www.ozbargain.com.au/node/1",
                "sourceURL": "https://www.coles.com.au/gift-cards",
                "tags": ["Gift Card", "Reward Points"],
                "pubDate": "2026-07-14T00:00:00+00:00",
                "expiry": "2026-07-21T13:59:00+00:00",
                "votesPos": 50,
                "votesNeg": 0,
                "sourceFeed": "test",
            }
        ]
        mock = normalize.MockLLM(
            [
                {
                    "retailer": "Coles",
                    "productOrBrand": "Gift cards",
                    "programs": ["flybuys"],
                    "pointsType": "storeRewards",
                    "offerKind": "multiplier",
                    "multiplier": 20.0,
                    "discountText": None,
                    "channel": "inStore",
                    "editorialNote": "Nice multiplier run.",
                    "confidence": 0.95,
                }
            ]
        )

        normalized = [normalize.normalize_candidate(c, mock) for c in candidates]
        normalized = [n for n in normalized if n is not None]

        self.assertEqual(len(normalized), 1)
        promo = normalized[0]
        self.assertEqual(promo["retailer"], "Coles")
        self.assertEqual(promo["programs"], ["flybuys"])
        self.assertEqual(promo["pointsType"], "storeRewards")
        self.assertEqual(promo["offerKind"], "multiplier")
        self.assertEqual(promo["multiplier"], 20.0)
        self.assertEqual(promo["validTo"], "2026-07-21T13:59:00+00:00")
        self.assertTrue(promo["id"].startswith("ozb-"))

        # Must validate cleanly against the schema (minus the internal _confidence field)
        clean = {k: v for k, v in promo.items() if not k.startswith("_")}
        errors = validate.validate_promo(clean, 0)
        self.assertEqual(errors, [])

    def test_skip_response_is_dropped(self):
        candidate = {
            "title": "10% cashback",
            "description": "",
            "link": "https://example.com",
            "sourceURL": "https://example.com",
            "tags": [],
            "pubDate": "2026-07-14T00:00:00+00:00",
            "expiry": "2026-08-01T00:00:00+00:00",
            "votesPos": 20,
            "votesNeg": 0,
            "sourceFeed": "test",
        }
        mock = normalize.MockLLM([{"skip": True, "reason": "not a points deal"}])
        result = normalize.normalize_candidate(candidate, mock)
        self.assertIsNone(result)

    def test_low_confidence_is_dropped(self):
        candidate = {
            "title": "Maybe points?",
            "description": "",
            "link": "https://example.com",
            "sourceURL": "https://example.com",
            "tags": [],
            "pubDate": "2026-07-14T00:00:00+00:00",
            "expiry": "2026-08-01T00:00:00+00:00",
            "votesPos": 20,
            "votesNeg": 0,
            "sourceFeed": "test",
        }
        mock = normalize.MockLLM(
            [
                {
                    "retailer": "Somewhere",
                    "programs": [],
                    "pointsType": "storeRewards",
                    "offerKind": "discount",
                    "discountText": "unclear",
                    "confidence": 0.3,
                }
            ]
        )
        result = normalize.normalize_candidate(candidate, mock)
        self.assertIsNone(result)

    def test_unmappable_program_is_dropped(self):
        candidate = {
            "title": "Bonus points somewhere",
            "description": "",
            "link": "https://example.com",
            "sourceURL": "https://example.com",
            "tags": [],
            "pubDate": "2026-07-14T00:00:00+00:00",
            "expiry": "2026-08-01T00:00:00+00:00",
            "votesPos": 20,
            "votesNeg": 0,
            "sourceFeed": "test",
        }
        mock = normalize.MockLLM(
            [
                {
                    "retailer": "Somewhere",
                    "programs": ["notARealProgram"],
                    "pointsType": "storeRewards",
                    "offerKind": "discount",
                    "discountText": "10% off",
                    "confidence": 0.9,
                }
            ]
        )
        result = normalize.normalize_candidate(candidate, mock)
        self.assertIsNone(result)

    def test_missing_expiry_is_dropped(self):
        candidate = {
            "title": "Bonus points, no expiry",
            "description": "",
            "link": "https://example.com",
            "sourceURL": "https://example.com",
            "tags": [],
            "pubDate": "2026-07-14T00:00:00+00:00",
            "expiry": None,
            "votesPos": 20,
            "votesNeg": 0,
            "sourceFeed": "test",
        }
        mock = normalize.MockLLM(
            [
                {
                    "retailer": "Somewhere",
                    "programs": [],
                    "pointsType": "storeRewards",
                    "offerKind": "discount",
                    "discountText": "10% off",
                    "confidence": 0.9,
                }
            ]
        )
        result = normalize.normalize_candidate(candidate, mock)
        self.assertIsNone(result)


class DedupeMergeTests(unittest.TestCase):
    def test_drops_expired_candidate(self):
        past = "2020-01-01T00:00:00Z"
        promo = {"validTo": past}
        now = datetime.now(timezone.utc)
        self.assertTrue(dedupe_merge.is_expired(promo, now))

    def test_drops_duplicate_of_live_promo(self):
        live = [
            {
                "id": "seed-wow-giftcard-20x",
                "retailer": "Woolworths",
                "offerKind": "multiplier",
                "multiplier": 20.0,
                "validFrom": "2026-07-14T00:00:00Z",
                "validTo": "2026-07-27T23:59:59Z",
            }
        ]
        candidate = {
            "id": "ozb-woolworths-abc123",
            "retailer": "Woolworths",
            "offerKind": "multiplier",
            "multiplier": 20.0,
            "validFrom": "2026-07-15T00:00:00Z",
            "validTo": "2026-07-20T00:00:00Z",
        }
        self.assertTrue(dedupe_merge.is_duplicate(candidate, live))

    def test_keeps_distinct_offer_same_retailer(self):
        live = [
            {
                "id": "seed-wow-giftcard-20x",
                "retailer": "Woolworths",
                "offerKind": "multiplier",
                "multiplier": 20.0,
                "validFrom": "2026-07-14T00:00:00Z",
                "validTo": "2026-07-27T23:59:59Z",
            }
        ]
        candidate = {
            "id": "ozb-woolworths-def456",
            "retailer": "Woolworths",
            "offerKind": "multiplier",
            "multiplier": 8.0,  # different offer value
            "validFrom": "2026-07-15T00:00:00Z",
            "validTo": "2026-07-20T00:00:00Z",
        }
        self.assertFalse(dedupe_merge.is_duplicate(candidate, live))

    def test_ensure_unique_ids_avoids_collision(self):
        candidates = [{"id": "seed-wow-giftcard-20x"}]
        existing = {"seed-wow-giftcard-20x"}
        result = dedupe_merge.ensure_unique_ids(candidates, existing)
        self.assertEqual(result[0]["id"], "seed-wow-giftcard-20x-2")

    def test_end_to_end_against_live_catalogue(self):
        future_valid_from = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        future_valid_to = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        candidates = [
            {
                "id": "ozb-newretailer-xyz",
                "retailer": "Totally New Retailer",
                "productOrBrand": None,
                "programs": ["qantasFF"],
                "pointsType": "frequentFlyer",
                "offerKind": "multiplier",
                "multiplier": 12.0,
                "discountText": None,
                "channel": "online",
                "validFrom": future_valid_from,
                "validTo": future_valid_to,
                "sourceURL": "https://example.com/deal",
                "editorialNote": None,
                "lastVerified": future_valid_from,
                "_confidence": 0.8,
            }
        ]

        with open(LIVE_CATALOGUE) as f:
            live = json.load(f)

        candidates = [c for c in candidates if not dedupe_merge.is_expired(c, datetime.now(timezone.utc))]
        live_active = [p for p in live if not dedupe_merge.is_expired(p, datetime.now(timezone.utc))]
        candidates = [c for c in candidates if not dedupe_merge.is_duplicate(c, live_active)]
        existing_ids = {p["id"] for p in live}
        candidates = dedupe_merge.ensure_unique_ids(candidates, existing_ids)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "ozb-newretailer-xyz")


class ValidateTests(unittest.TestCase):
    def test_real_catalogue_passes(self):
        with open(LIVE_CATALOGUE) as f:
            promos = json.load(f)
        errors = validate.validate_catalogue(promos)
        self.assertEqual(errors, [], msg=f"unexpected validation errors: {errors}")

    def test_missing_field_fails(self):
        with open(os.path.join(FIXTURES_DIR, "bad_missing_field.json")) as f:
            promos = json.load(f)
        errors = validate.validate_catalogue(promos)
        self.assertTrue(errors)
        self.assertTrue(any("missing" in e or "validTo" in e for e in errors))

    def test_bad_dates_fails(self):
        with open(os.path.join(FIXTURES_DIR, "bad_dates.json")) as f:
            promos = json.load(f)
        errors = validate.validate_catalogue(promos)
        self.assertTrue(errors)
        self.assertTrue(any("validTo" in e for e in errors))

    def test_bad_program_fails(self):
        with open(os.path.join(FIXTURES_DIR, "bad_program.json")) as f:
            promos = json.load(f)
        errors = validate.validate_catalogue(promos)
        self.assertTrue(errors)
        self.assertTrue(any("program" in e for e in errors))

    def test_duplicate_id_fails(self):
        with open(os.path.join(FIXTURES_DIR, "bad_duplicate_id.json")) as f:
            promos = json.load(f)
        errors = validate.validate_catalogue(promos)
        self.assertTrue(errors)
        self.assertTrue(any("duplicate id" in e for e in errors))

    def test_cli_exit_codes(self):
        argv_backup = sys.argv
        try:
            sys.argv = ["validate.py", LIVE_CATALOGUE]
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = validate.main()
            self.assertEqual(exit_code, 0)

            sys.argv = ["validate.py", os.path.join(FIXTURES_DIR, "bad_dates.json")]
            exit_code = validate.main()
            self.assertEqual(exit_code, 1)
        finally:
            sys.argv = argv_backup


class RenderPrTests(unittest.TestCase):
    def test_renders_table_with_expected_columns(self):
        promos = [
            {
                "retailer": "Coles",
                "offerKind": "multiplier",
                "multiplier": 20.0,
                "programs": ["flybuys"],
                "validTo": "2026-07-21T00:00:00Z",
                "sourceURL": "https://example.com/coles",
                "_confidence": 0.91,
            },
            {
                "retailer": "Kathmandu",
                "offerKind": "discount",
                "discountText": "25% off",
                "programs": [],
                "validTo": "2026-07-23T00:00:00Z",
                "sourceURL": "https://example.com/kathmandu",
                "_confidence": 0.75,
            },
        ]
        table = render_pr.render_table(promos)
        self.assertIn("Retailer", table)
        self.assertIn("Coles", table)
        self.assertIn("20x", table)
        self.assertIn("Kathmandu", table)
        self.assertIn("25% off", table)
        self.assertIn("flybuys", table)
        self.assertIn("0.91", table)
        self.assertIn("0.75", table)

    def test_empty_list_renders_placeholder(self):
        table = render_pr.render_table([])
        self.assertIn("Catalogue is empty", table)

    def test_new_rows_are_flagged(self):
        promos = [
            {"retailer": "Coles", "offerKind": "multiplier", "multiplier": 20.0,
             "programs": ["flybuys"], "validTo": "2026-08-01T00:00:00Z",
             "sourceURL": "https://example.com/c", "_isNew": True},
            {"retailer": "Kathmandu", "offerKind": "discount", "discountText": "25% off",
             "programs": [], "validTo": "2026-07-23T00:00:00Z",
             "sourceURL": "https://example.com/k", "_isNew": False},
        ]
        table = render_pr.render_table(promos)
        self.assertIn("🆕", table)
        self.assertIn("1 new", table)


class RollCatalogueTests(unittest.TestCase):
    NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)

    def _promo(self, pid, days, is_new_conf=None, kind="discount"):
        valid_to = (self.NOW + timedelta(days=days)).isoformat()
        p = {"id": pid, "retailer": pid, "offerKind": kind,
             "discountText": "10% off", "multiplier": None, "programs": [],
             "pointsType": "storeRewards", "validTo": valid_to,
             "validFrom": self.NOW.isoformat(), "sourceURL": "https://x.com",
             "channel": None, "editorialNote": None, "lastVerified": self.NOW.isoformat(),
             "productOrBrand": None}
        if is_new_conf is not None:
            p["_confidence"] = is_new_conf
        return p

    def test_expired_live_promos_are_dropped(self):
        live = [self._promo("keep", 10), self._promo("gone", -1)]
        rolled = roll_catalogue.roll(live, [], self.NOW, target=10)
        ids = {p["id"] for p in rolled}
        self.assertIn("keep", ids)
        self.assertNotIn("gone", ids)

    def test_expired_new_candidate_is_dropped(self):
        rolled = roll_catalogue.roll([], [self._promo("old", -2, 0.9)], self.NOW, target=10)
        self.assertEqual(rolled, [])

    def test_caps_at_target(self):
        live = [self._promo(f"p{i}", 30 + i) for i in range(20)]
        rolled = roll_catalogue.roll(live, [], self.NOW, target=10)
        self.assertEqual(len(rolled), 10)

    def test_new_take_priority_over_survivors(self):
        live = [self._promo(f"old{i}", 30) for i in range(10)]
        new = [self._promo("fresh", 5, 0.9)]
        rolled = roll_catalogue.roll(live, new, self.NOW, target=10)
        ids = {p["id"] for p in rolled}
        self.assertIn("fresh", ids)
        self.assertEqual(len(rolled), 10)

    def test_new_flag_and_internal_fields_present_on_roll(self):
        new = [self._promo("fresh", 5, 0.9)]
        rolled = roll_catalogue.roll([], new, self.NOW, target=10)
        self.assertTrue(rolled[0]["_isNew"])
        self.assertEqual(rolled[0]["_confidence"], 0.9)

    def test_strip_internal_removes_underscore_fields(self):
        cleaned = roll_catalogue.strip_internal({"id": "x", "_confidence": 0.9, "_isNew": True})
        self.assertEqual(cleaned, {"id": "x"})

    def test_dedup_between_new_and_live_by_id(self):
        shared = self._promo("dup", 20, 0.9)
        live = [dict(shared)]
        new = [dict(shared)]
        rolled = roll_catalogue.roll(live, new, self.NOW, target=10)
        self.assertEqual(len([p for p in rolled if p["id"] == "dup"]), 1)


if __name__ == "__main__":
    unittest.main()
