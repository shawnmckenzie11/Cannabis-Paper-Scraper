# Calibration Walkthrough

- Batch ID: `calibration_20260622_173709_593`
- Created: `2026-06-22T17:37:38.217368`
- Rules version: `2.6.0`
- Candidate mode: `node2b_in_vivo`
- Automation node: `node2b`
- Dry run: `True`
- Abstract only: `False`
- Planned candidates: `10` / `10`
- Updates applied: `0`
- Estimated API cost: `$0.0000`

## Variant Allocation

- `llm-pdf-reclassify`: 10 papers, 0 updates, avg confidence 0.902, cost $0.0000

## Most Changed Fields

- No field changes recorded.

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
