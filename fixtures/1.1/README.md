# fixtures/1.1 — real seeded estates + golden keys (the eval dataset)
Clone real, public, diverse estates. Golden keys are human-authored + reviewed. Living asset — add real failures back over time. Keep ~100 goldens total, quality over volume.

- `estate-messy/` — a real multi-language repo with dynamic dispatch/config routing.
- `estate-single/` — a small single repo.
- `estate-monorepo/` — a large monorepo (scale).
- `estate-docs/` — a docs-heavy repo (Markdown + PDFs).
- `estate-secrets/` — a repo with 20 known injected secrets → `golden/secrets.json`.
- `estate-hard/` — 3 scanned PDFs, 2 borderless tables, 2 diagrams → `golden/hard/`.
- `estate-perms/` — 2 principals + access matrix → `golden/access-matrix.json`.
- `golden/citations-sample.json` — human-verified correct locators (P2).
Record provenance + license per estate. Public/licensed sources only.
