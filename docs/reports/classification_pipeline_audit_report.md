# Classification Pipeline Audit Report

**System:** Cannabis Paper Scraper | **App:** cannabis-paper-scraper (Fly.io) | **Date:** 28 June 2026  
**Maude build ID:** `20260628-manual-edit-tracker-v2`

---

## Executive Summary

This audit examines the Cannabis Paper Scraper classification pipeline: a node-based literature system that harvests PubMed records, routes them through Maude (deterministic) and LLM-assisted classifiers, and improves accuracy through controlled RL cycles. The decision tree runs Node 0 (relevance gate), Node 1B/1A (secondary literature before original research), and Node 2A/2B/2C (clinical, in vivo, in vitro extraction).

As of 28 June 2026, eleven golden-endpoint cycles completed with guard validation passed. Batch alignment ranges from 90.0% to 100.0% (mean 92.2%) against a ≥90% guard threshold. Golden row 0 (`node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media`) reached 100% alignment after three guard iterations, with subnode reingest changing 3,162 of 3,778 papers and cohort routing improving by +78 within a 1,420-paper pool. Six node2b endpoints passed guard at 90.0–92.2% alignment. Quality gates, build traceability, and artifact logging are in place; 67 of 78 golden-table rows remain uncycled.

## Architecture Overview

The design specification defines five principles: Maude for cheap deterministic extraction; LLM and PDF tier for ambiguity; sub-threshold records to expert review; locked expert corrections; and same-holdout re-measurement after every deploy. Each record stores classification source, version/build ID, confidence, and timestamp. Production writes target Fly Postgres; the active build is `MAUDE_CLASSIFIER_BUILD_ID = "20260628-manual-edit-tracker-v2"` in `calibration_build.py`.

## Learning Loops

**Loop A (subnode calibration).** PDF Maude A/B on Fly → feedback → patch → build bump → deploy → same-holdout refresh → subnode reingest and blast-radius via `run_loop_a_finish.sh`.

**Loop B (golden endpoint RL).** Per golden-table row (78 total): pull → LLM-label 10 → promote top 5 → Claude feedback → golden guard (≥90%) → reingest → push. Row *n* blocked until prior rows show `guard_passed: true`.

**Manual edit cycle.** `manual_edit_cycle.py` harvests expert-drawer edits from `feedback_audit` and routes them into the Loop A feedback pipeline between scheduled batches.

## Quality Assurance & Metrics

- **Golden guard:** 11/11 completed endpoints passed; iterations 1–6.
- **Blast radius (row 0):** 3,778 scanned, 3,162 changed, 7,142 field changes, 21/28 characteristics.
- **Cohort validation (row 0):** pool 1,420; routing 1,279 → 1,357 (+78).
- **Upward propagation:** BM25 over `feedback_audit_fts` injects prior expert corrections at classify time.

## Evidence from Completed Runs

**Row 0:** 100% alignment, 3 guard iterations, 486 deltas, build `20260628-manual-edit-tracker-v2`.

**Node2b endpoints:**

| Endpoint | Alignment | Guard iter. | Deltas |
|----------|-----------|-------------|--------|
| mouse.injection_cannabinoids | 92.2% | 6 | 497 |
| rat.injection_cannabinoids | 92.2% | 2 | 5,831 |
| mouse.cannabinoids_dissolved_in_media | 92.2% | 2 | 486 |
| other.cannabinoids_dissolved_in_media | 90.0% | 1 | 5,820 |
| other.injection_cannabinoids | 90.5% | 5 | 5,820 |
| rat.cannabinoids_dissolved_in_media | 90.4% | 3 | 5,823 |

## Limitations & Safeguards

Small holdouts, 67 uncycled rows, high subnode change rates (row 0: 83.7%). Safeguards: build-ID preflight, golden guard, row gating, expert locking, zero-regression gate, artifact logging.

## Conclusion

The pipeline provides auditable separation of routing, extraction, expert review, and rule refinement with repeatable ≥90% guard passage and transparent blast-radius reporting.

---

*Sources: golden_endpoint_status.json, blast_radius.json (node2c 20260628_161206), calibration_build.py, architecture-design-document.md.*
