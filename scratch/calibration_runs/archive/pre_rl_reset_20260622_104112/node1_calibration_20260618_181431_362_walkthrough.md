# Calibration Walkthrough

- Batch ID: `node1_calibration_20260618_181431_362`
- Created: `2026-06-18T18:16:23.636462`
- Rules version: `2.3.0`
- Candidate mode: `node1_routing`
- Automation node: `node1`
- Dry run: `False`
- Abstract only: `True`
- Claude classification attempts used: `20` / `20`
- Updates applied: `20`
- Estimated API cost: `$0.1733`

## Variant Allocation

- `control`: 10 papers, 10 updates, avg confidence 0.876, cost $0.0851
- `decision_checklist`: 10 papers, 10 updates, avg confidence 0.842, cost $0.0883

## Most Changed Fields

- `exposure_method`: 19 changed papers
- `outcome_domain`: 10 changed papers
- `cannabis_type`: 6 changed papers
- `study_type`: 3 changed papers
- `cbd_mg_kg`: 2 changed papers
- `duration_days`: 2 changed papers
- `dose_mg`: 1 changed papers
- `administration_frequency`: 1 changed papers
- `publication_type`: 1 changed papers

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
