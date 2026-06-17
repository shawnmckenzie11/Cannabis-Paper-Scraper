# Calibration Walkthrough

- Batch ID: `calibration_20260616_231015`
- Created: `2026-06-16T23:15:49.626468`
- Rules version: `2.2.0`
- Candidate mode: `preclinical_original`
- Dry run: `False`
- Abstract only: `True`
- Claude classification attempts used: `50` / `50`
- Updates applied: `50`
- Estimated API cost: `$0.5402`

## Variant Allocation

- `control`: 25 papers, 25 updates, avg confidence 0.707, cost $0.2680
- `decision_checklist`: 25 papers, 25 updates, avg confidence 0.690, cost $0.2722

## Most Changed Fields

- `cannabis_type`: 45 changed papers
- `exposure_method`: 36 changed papers
- `study_type`: 35 changed papers
- `strain_reported`: 34 changed papers
- `outcome_domain`: 20 changed papers
- `strain_normalized`: 10 changed papers
- `sample_size`: 5 changed papers
- `cbd_uM`: 5 changed papers
- `duration_days`: 4 changed papers
- `administration_frequency`: 4 changed papers
- `treatment_duration`: 4 changed papers
- `thc_mg_kg`: 3 changed papers
- `thc_uM`: 3 changed papers
- `inhaled_exposure_duration`: 2 changed papers
- `thc_mg_ml`: 2 changed papers
- `cbd_mg_kg`: 1 changed papers
- `publication_type`: 1 changed papers
- `dose_mg`: 1 changed papers

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
