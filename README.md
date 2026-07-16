# dispoint-catalogue

Curated + auto-sourced promo catalogue for the DisPoint iOS app. The app reads
`catalogue/catalogue.json` (raw). A weekly GitHub Action fetches OzBargain deals,
normalizes them via Claude, and opens a PR for review. See `catalogue/README.md`.
