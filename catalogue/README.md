# DisPoint catalogue pipeline

Weekly, serverless sourcing pipeline that fetches AU points/loyalty deals from OzBargain,
normalizes them into the app's `CataloguePromo` schema via Claude, dedupes against the
live catalogue, validates, and opens a GitHub PR for a ~5-minute human review.

## `CataloguePromo` field reference

Matches `Shared/Models/CataloguePromo.swift` exactly.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable, unique across the catalogue |
| `retailer` | string | e.g. "Coles" |
| `productOrBrand` | string \| null | e.g. "Apple gift cards" |
| `programs` | string[] | Subset of: `everydayRewards`, `flybuys`, `qantasFF`, `velocityFF`, `onePass`, `pricelineSisterClub`, `myerOne`, `amexMembershipRewards` |
| `pointsType` | `"storeRewards"` \| `"frequentFlyer"` | |
| `offerKind` | `"multiplier"` \| `"discount"` | Decides which fields below apply |
| `multiplier` | number \| null | Required (non-null) when `offerKind == "multiplier"` |
| `discountText` | string \| null | Required (non-null) when `offerKind == "discount"` |
| `channel` | `"online"` \| `"inStore"` \| `"both"` \| null | |
| `validFrom` | ISO-8601 string | |
| `validTo` | ISO-8601 string | Must be after `validFrom`; expiry |
| `sourceURL` | string | Well-formed `http(s)` URL |
| `editorialNote` | string \| null | Short curator note |
| `lastVerified` | ISO-8601 string | |

## One-time setup

1. **Create a PUBLIC GitHub repo** named `dispoint-catalogue` (public so the app can fetch
   the raw JSON without auth).
2. **Push the contents of this `catalogue/` directory** to that repo's default branch (top-level,
   so paths in the workflow — `catalogue/catalogue.json`, `catalogue/pipeline/*.py` — line up),
   **and move the workflow into place**: copy `catalogue/weekly-catalogue.yml.template` to
   `.github/workflows/weekly-catalogue.yml` in the new repo. (It ships here as a `.template` so it
   does NOT run inside the private app repo — the pipeline is meant to run only in the public
   catalogue repo.)
3. **Add the `ANTHROPIC_API_KEY` secret** to the new repo: Settings → Secrets and variables →
   Actions → New repository secret → name `ANTHROPIC_API_KEY`.
4. **Point the app at the published catalogue.** Set `Constants.catalogueURL` to the raw file
   URL, e.g.:
   ```
   https://raw.githubusercontent.com/<you>/dispoint-catalogue/main/catalogue/catalogue.json
   ```
5. Confirm the workflow is enabled (Actions tab) and optionally trigger it once manually via
   **Run workflow** (`workflow_dispatch`) to verify the setup end-to-end.

## Weekly review (~5 minutes)

The workflow runs every **Sunday 20:00 UTC** (and on manual `workflow_dispatch`). If it finds
new candidate promos:

1. Open the PR it created (title: "Weekly catalogue update (N new candidate promos)").
2. Scan the rendered table — retailer, offer, programs, valid-to date, source link, confidence.
3. Drop any row that looks wrong: edit `catalogue/catalogue.json` directly in the PR to remove
   the entry (or comment and let CI re-run next week).
4. Merge. The merge updates `catalogue.json` on the default branch, which is what the app
   fetches — no separate deploy step.

**Note:** low-confidence (< 0.6) and already-expired candidates are pre-dropped by the
pipeline before you ever see the PR — the table only shows the survivors.

If the workflow finds no new candidates that week, it does not open a PR — nothing to review.

## Manual / one-off additions

`catalogue/catalogue.json` remains hand-editable at any time — this is the fallback path,
independent of the weekly pipeline. After editing, validate locally:

```sh
python3 catalogue/pipeline/validate.py catalogue/catalogue.json
```

## Pipeline internals

| File | Role |
|---|---|
| `pipeline/fetch.py` | Pulls OzBargain tag feeds (gift-card, frequent-flyer, flybuys, velocity, qantas, everyday-rewards), filters by vote score and expiry, emits raw candidates as JSON |
| `pipeline/normalize.py` | Calls Claude (`claude-sonnet-5`) to map each raw candidate into the `CataloguePromo` schema + a confidence score; drops non-points deals and low-confidence/un-mappable items |
| `pipeline/dedupe_merge.py` | Drops candidates duplicating a live promo (same retailer + similar offer + overlapping dates) or already expired; assigns unique ids |
| `pipeline/validate.py` | Schema gate — required fields, ISO dates, `validTo > validFrom`, known program names, offerKind/value consistency, unique ids, well-formed URLs. Runnable standalone. |
| `pipeline/render_pr.py` | Renders the markdown review table for the PR body |

Run the full chain locally (no API key needed if you pass `--mock-response-file`):

```sh
python3 catalogue/pipeline/fetch.py --feed-file catalogue/tests/sample_ozbargain_feed.xml \
  | python3 catalogue/pipeline/normalize.py --mock-response-file catalogue/tests/fixtures/mock_llm_responses.json \
  | tee /tmp/normalized.json \
  | python3 catalogue/pipeline/dedupe_merge.py --catalogue catalogue/catalogue.json --candidates /dev/stdin
```
