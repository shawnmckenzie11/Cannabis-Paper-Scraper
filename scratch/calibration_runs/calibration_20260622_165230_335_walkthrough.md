# Calibration Walkthrough

- Batch ID: `calibration_20260622_165230_335`
- Created: `2026-06-22T16:52:51.403105`
- Rules version: `2.6.0`
- Candidate mode: `node2a_clinical`
- Automation node: `node2a`
- Dry run: `True`
- Abstract only: `False`
- Planned candidates: `10` / `10`
- Updates applied: `0`
- Estimated API cost: `$0.0000`

## Variant Allocation

- `llm-pdf-reclassify`: 10 papers, 0 updates, avg confidence 0.705, cost $0.0000

## Most Changed Fields

- No field changes recorded.

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
