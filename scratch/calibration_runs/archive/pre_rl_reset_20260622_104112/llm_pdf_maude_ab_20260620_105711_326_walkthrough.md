# Calibration Walkthrough

- Batch ID: `llm_pdf_maude_ab_20260620_105711_326`
- Created: `2026-06-20T10:57:11.796389`
- Rules version: `2.4.0`
- Candidate mode: `llm_pdf_maude_ab`
- Automation node: `node1`
- Dry run: `True`
- Abstract only: `False`
- Planned candidates: `12` / `12`
- Updates applied: `0`
- Estimated API cost: `$0.0000`

## Variant Allocation

- `llm-pdf-reclassify`: 10 papers, 0 updates, avg confidence 0.899, cost $0.0000
- `llm-reclassify`: 2 papers, 0 updates, avg confidence 0.683, cost $0.0000

## Most Changed Fields

- No field changes recorded.

## Next Agent Actions

1. Review the JSON artifact for papers with large changes in high-level fields.
2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.
3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.
4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.
