# Calibration Walkthrough

- Batch ID: `calibration_20260616_222616`
- Created: `2026-06-16T22:31:25.740023`
- Rules version: `2.2.0`
- Candidate mode: `preclinical_original`
- Dry run: `False`
- Abstract only: `True`
- Claude classification attempts used: `50` / `50`
- Updates applied: `50`
- Estimated API cost: `$0.5223`

## Variant Allocation

- `control`: 25 papers, 25 updates, avg confidence 0.724, cost $0.2600
- `decision_checklist`: 25 papers, 25 updates, avg confidence 0.720, cost $0.2624

## Most Changed Fields

- `cannabis_type`: 48 changed papers
- `strain_reported`: 37 changed papers
- `study_type`: 21 changed papers
- `exposure_method`: 21 changed papers
- `outcome_domain`: 12 changed papers
- `administration_frequency`: 11 changed papers
- `strain_normalized`: 10 changed papers
- `cbd_mg_kg`: 7 changed papers
- `thc_mg_kg`: 5 changed papers
- `cbd_uM`: 3 changed papers
- `treatment_duration`: 3 changed papers
- `duration_days`: 3 changed papers
- `thc_uM`: 2 changed papers
- `publication_type`: 1 changed papers
- `dose_mg`: 1 changed papers
- `cbd_pct`: 1 changed papers
- `sample_size`: 1 changed papers

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
