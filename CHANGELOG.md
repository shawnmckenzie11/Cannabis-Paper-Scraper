# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.6.0] - 2026-06-22

### Added
- **Maude 2.6.0 bulk reclassification** of all original-research papers tagged `maude-*` or `heuristic-*` that were not LLM-classified (~15,000 papers on production).
- `reingest_heuristic_papers.py --maude-and-heuristic` and `scheduled_jobs.py run-maude-reingest-now` for immediate or scheduled full-corpus Maude refresh.
- `paper_text_cache.py` lazy disk cache for PDF/full-text resolution during batch re-ingest (persists on Fly `/data`).
- Tier-scoped RL alignment gates (`content_tiers.alignment_fields_in_scope_for_tier`) and `audit_tier_field_gaps.py` for field-subset holdout scoring.
- Node 2a/2b/2c RL handoffs through build `20260622-node2a-203356-clinical-v1` (node2a 96.4%, node2c 98.3% holdout alignment; strain excluded from gate).

### Changed
- Expanded `extractor.py` and `maude_classifier.py` with RL-tuned extraction for clinical, in vivo, and in vitro tiers (cannabis type, exposure, strain, dose mg/kg, duration, frequency).
- Re-ingest stamps papers with tiered versions: `maude-pdf-2.6.0`, `maude-fulltext-2.6.0`, or `maude-2.6.0` (replacing legacy `maude-reclassify-*` and `heuristic-reclassify-*` labels).
- Post-run `classification_confidence` refresh on all `maude-*` papers from node alignment percentages.
- RL alternating loop uses holdout field-subset gate mode and optional offset-0 generalization every 3 cycles.

## [1.2.2] - 2026-06-13

### Fixed
- Resolved a Google sign-in `TypeError: 'NoneType' object is not subscriptable` error by implementing unique username generation to prevent UNIQUE constraint collisions on user creation.
- Added user retrieval safety checks to ensure a clean error page is served if database synchronization fails during authentication.
- Removed the redundant description prefix `The Learning Dashboard feature is locked in the MVP release.` from the MVP Lock Modal when locked features are clicked.

## [1.2.1] - 2026-06-13

### Changed
- Added `solutions@mckenzian.com` and `nadiadalim@gmail.com` to the default set of administrator email accounts.

## [1.2.0] - 2026-06-13

### Added
- Introduced `@admin_required` backend decorator to restrict catalog write operations (`/api/papers/delete`, `/api/papers/<id>/edit-classification`, `/api/papers/<id>/reclassify-llm`) to authorized administrator accounts.
- Configured the list of administrator emails to support `shawnmckenzie11.sm@gmail.com` by default and dynamically extend via the `ADMIN_EMAILS` environment variable.
- Added frontend locking UI for Delete, Edit, and Reclassify drawer buttons with a `🔒` indicator for non-admin users.
- Implemented an "Admin Access Required" glassmorphic modal to present clear guidance to logged-in non-admin users.
- Added `TestAdminRequiredEndpoints` class verifying admin/non-admin/anonymous constraints.

## [1.1.2] - 2026-06-13

### Changed
- Added `prompt=select_account` to Google OAuth parameters to force the account selector popup.
- Set `data-auto_select` to `false` in Google One Tap configuration to prevent automatic login on page load.

## [1.1.1] - 2026-06-13

### Fixed
- Resolved a `TypeError: '<' not supported between instances of 'str' and 'int'` on the `/api/analyze` endpoint by coercing all publication timeline year keys to strings.

## [1.1.0] - 2026-06-13

### Added
- Integrated Google Identity Services One Tap / Auto Sign-In (`auto_select=true`) on the login screen.
- Supported POST request handling on `/auth/google/callback` to verify and parse Google JWT credentials.
- Isolated saved analyses on a per-user basis in the database and added ownership checking.
- Allowed public (logged-out) users to run subset analyses and edit columns without database persistence (returns `id: null`).
- Created `TestAnalysesUserIsolation` test cases checking public access and user ownership bounds.

### Changed
- Simplified fallback simulated Google Sign-In, removing pre-filled mock user accounts.
- Removed login gate interceptors from subset analyses and column editor buttons in `templates/index.html`.

## [1.0.0-mvp] - 2026-06-13

### Added
- Created backend decorator `@mvp_gate` in `app.py` to protect restricted API endpoints.
- Implemented visual lock modal (`#mvp-lock-modal`) and lock badges (`🔒`) in `templates/index.html` for Harvest, Learning Dashboard, and Connectivity buttons.
- Created `TestMvpGatingAPI` suite in `test_suite.py` to assert correct `403 Forbidden` responses.
- Pinned frontend `Chart.js` CDN library dependency to `v4.4.2` to prevent breaking upgrades.
- Supported micromolar concentration extractions (`thc_uM` and `cbd_uM`) for preclinical (in vitro) studies.
- Added self-optimizing reinforcement learning prompt loop based on reliability metrics.
- Added GPR55/LPI receptor "dud" paper filtering and auto-deletion/purging functionality.
