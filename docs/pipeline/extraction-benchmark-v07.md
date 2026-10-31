# Rule-based extraction + main-text completeness benchmark (contract v0.7)

**Owner:** CRN Pipeline. **Contract:** Methods v0.7 on main (`e56de84`, #117). Tip/dev only; no Fly, no prod, no DATABASE_URL, no paid API (extraction budget cap = 0).

## Approach
No LLM. `crn_extraction/extractor.py` is deterministic: regex cues in `crn_extraction/extraction_config.json` over sentence-split **main text** (abstract, introduction, methods, results, discussion, tables, figure captions). Vocabularies (route, product_form, cannabinoid, duration_category) are read at runtime from `schemas/normalization_rules.json`; enums from `schemas/methods.schema.json`. Cues were written from contract text and fake fixtures only — no gold, qrels, grades, split labels or `legacy_llm_fullpdf` values.

Rules: record only what the paper says; never fill gaps, never use product labels/monographs, never fetch cited papers; no tier is computed (Methods' `methods_completeness.compute` does that; defaults 3/6/8 are not applied here). Every record is validated against the full schema.

## Sources
`crn_extraction/sources.py`: PMC Open Access JATS XML only (stdlib XML parser). Supplementary material, acknowledgements, references and the contributor block are dropped and never fetched. NCBI: 2.5 req/s, ≤200 ids per call, `tool=crn_pipeline`, placeholder email, no API key; neither is written to provenance.

## Reason codes
| Code | Emitted when |
|---|---|
| `fetch_failed` | network/HTTP error fetching main text → every item could_not_check |
| `parse_failed` | JATS XML does not parse → every item could_not_check |
| `source_not_available` | no PMC OA full text (no PMC link / abstract only) → every item could_not_check; manifest excluded `source_unavailable` |
| `budget_cap` | paper past `--max-papers` → every item could_not_check; manifest excluded `budget_cap` |
| `source_not_fetched` | item not found, and a main-text sentence with the item's cue points to a supplement (Table S1, Supplementary …) |
| `figure_not_read` | item not found, and a sentence with the item's cue points to a figure (figure images are never read) |
| `low_confidence_read` | dose/concentration only in a flattened table row with mixed units |
| `missing_units` (TOP3) | dose/concentration number with no unit |
| `protocol_without_number` (C1) | "according to the protocol" with no number |
| `cited_elsewhere` (TOP1) | "as previously described [ref]" with the item's cue and no value |
| `vague_term_only` (C2) | duration term (vocab `duration_category`) with no actual time; `duration_category` + `_verbatim` set (also set alongside a reported time) |
| `government_program_unnamed` (P2) | supplier is an unnamed government/national program; wording in `fields.supplier_program_verbatim` |
| `product_named_content_missing` | product named (e.g. brand) but no cannabinoid in the main text |
| `absent` | nothing found (default) |

Also: C1 ranges count as reported (number field value left null, span kept — no number invented); `comparison_group` with `extraction_confidence`, `needs_review`/`review_reason`, `comparator_validated: false` always; `group_sample_sizes` only from numeric `X group (n = N)`; `value_source: paper` on every status (no `source_attribution`, that is legacy-only); `mesh_from_pubmed` copied from PubMed MeSH ids as cross-check only, never into `reporting.conditions`. Q7: surveys, market studies and re-analyses get no record and are listed in `excluded_not_original_research` of the benchmark summary.

## Benchmark
`python -m crn_extraction.benchmark --fetch` reads PMIDs **only** from `/workspace/crn-eval/dev_pmids.txt` (the 21 train+dev papers, written by Evaluation; test excluded). It refuses gold/qrels/grades/split-manifest/`manifest_pmids.txt` paths. If the file is missing it prints `pending` and exits 2. Output: per-item status counts, reason counts, schema-invalid records, and a contract `extraction_run_manifest` (`model_ids: {}` — no model). `--fixtures tests/fixtures/crn_extraction` runs on fake papers (fake PMIDs 9000000xx, no PII).

## Known limits
Number words ("Forty rats") are not read as sample sizes; regex cues are English-only; figure images are not read.
