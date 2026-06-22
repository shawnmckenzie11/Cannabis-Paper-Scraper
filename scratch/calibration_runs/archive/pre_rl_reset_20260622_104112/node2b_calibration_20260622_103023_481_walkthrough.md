# Calibration Walkthrough

- Batch ID: `node2b_calibration_20260622_103023_481`
- Created: `2026-06-22T10:30:23.481726`
- Rules version: `2.6.0`
- Candidate mode: `node2b_in_vivo`
- Automation node: `node2b`
- Dry run: `True`
- Abstract only: `True`
- Planned candidates: `3` / `3`
- Updates applied: `0`
- Estimated API cost: `$0.0000`

## Variant Allocation

- `control`: 3 papers, 0 updates, cost $0.0000

## Most Changed Fields

- No field changes recorded.

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
