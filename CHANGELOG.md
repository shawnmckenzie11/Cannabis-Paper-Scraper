# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-mvp] - 2026-06-13

### Added
- Created backend decorator `@mvp_gate` in `app.py` to protect restricted API endpoints.
- Implemented visual lock modal (`#mvp-lock-modal`) and lock badges (`🔒`) in `templates/index.html` for Harvest, Learning Dashboard, and Connectivity buttons.
- Created `TestMvpGatingAPI` suite in `test_suite.py` to assert correct `403 Forbidden` responses.
- Pinned frontend `Chart.js` CDN library dependency to `v4.4.2` to prevent breaking upgrades.
- Supported micromolar concentration extractions (`thc_uM` and `cbd_uM`) for preclinical (in vitro) studies.
- Added self-optimizing reinforcement learning prompt loop based on reliability metrics.
- Added GPR55/LPI receptor "dud" paper filtering and auto-deletion/purging functionality.
