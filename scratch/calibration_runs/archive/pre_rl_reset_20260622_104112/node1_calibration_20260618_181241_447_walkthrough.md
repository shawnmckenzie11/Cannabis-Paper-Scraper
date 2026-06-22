# Calibration Walkthrough

- Batch ID: `node1_calibration_20260618_181241_447`
- Created: `2026-06-18T18:14:24.769850`
- Rules version: `2.3.0`
- Candidate mode: `node1_routing`
- Automation node: `node1`
- Dry run: `False`
- Abstract only: `True`
- Claude classification attempts used: `20` / `20`
- Updates applied: `20`
- Estimated API cost: `$0.1694`

## Variant Allocation

- `control`: 10 papers, 10 updates, avg confidence 0.895, cost $0.0828
- `decision_checklist`: 10 papers, 10 updates, avg confidence 0.862, cost $0.0866

## Most Changed Fields

- `exposure_method`: 20 changed papers
- `outcome_domain`: 13 changed papers
- `cannabis_type`: 6 changed papers
- `sample_size`: 3 changed papers
- `study_type`: 2 changed papers
- `publication_type`: 2 changed papers
- `thc_pct`: 1 changed papers
- `strain_reported`: 1 changed papers
- `strain_normalized`: 1 changed papers
- `administration_frequency`: 1 changed papers
- `duration_days`: 1 changed papers

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
