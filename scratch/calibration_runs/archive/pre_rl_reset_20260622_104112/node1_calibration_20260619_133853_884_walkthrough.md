# Calibration Walkthrough

- Batch ID: `node1_calibration_20260619_133853_884`
- Created: `2026-06-19T13:38:54.447153`
- Rules version: `2.4.0`
- Candidate mode: `node1_routing`
- Automation node: `node1`
- Dry run: `True`
- Abstract only: `True`
- Planned candidates: `0` / `40`
- Updates applied: `0`
- Estimated API cost: `$0.3619`

## Variant Allocation

- `control`: 40 papers, 40 updates, avg confidence 0.837, cost $0.3619

## Most Changed Fields

- `exposure_method`: 39 changed papers
- `outcome_domain`: 21 changed papers
- `cannabis_type`: 14 changed papers
- `duration_days`: 3 changed papers
- `study_type`: 2 changed papers
- `sample_size`: 2 changed papers
- `administration_frequency`: 1 changed papers
- `dose_mg`: 1 changed papers
- `strain_reported`: 1 changed papers
- `strain_normalized`: 1 changed papers
- `publication_type`: 1 changed papers

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
