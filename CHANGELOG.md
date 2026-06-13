# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
