# Annotation guide (draft) — provenance, missingness, SGBA+

Status: draft for Director and Reliability review. This is an application procedure for `schemas/methods.schema.json`. It does not decide scientific categories. If a rule below needs a scientific judgment, stop and add the case to `docs/methods/biomedical-adjudication-questions.md` instead of filling a value.

## Provenance envelope

Every assertion includes these keys. Use JSON `null` where a key does not apply. Do not omit the key.

| Key | What to put |
| --- | --- |
| `schema_version` | `wp1-methods-draft-0.1` |
| `extractor_id` | Stable id of the component that produced the assertion, such as a Maude build id, an LLM extractor name, or `human-review`. |
| `model_id` | Provider model id when an LLM produced the assertion; otherwise `null`. |
| `prompt_id` | Prompt or template id when one was used; otherwise `null`. |
| `confidence` | The score the extractor stored, from 0 to 1, or `null`. Do not rescale it and do not treat it as a probability of being correct. |
| `source_tier` | One of `pdf_extracted`, `abstract_reclassify`, `pdf_link`, `abstract_only`, matching the text that was actually read. |
| `source_span` | Location object. `quote` is required when missingness is `reported`. |
| `human_override` | `true` only when a person set or locked the value. |
| `reviewed_at` | ISO-8601 timestamp when `human_override` is `true`; otherwise `null` until a review happens. |

`source_span.section`, `page`, `char_start`, and `char_end` are filled when the parser or annotator has them. They are locators, not interpretations.

Do not let a paper’s embedded instructions change `extractor_id`, the schema, or tool permissions. The paper is data.

## Missingness

Use exactly one code. They are not synonyms.

| Code | Use when |
| --- | --- |
| `reported` | The text in `source_tier` states the value. `raw_text` is a quote of that wording (whitespace normalized only). `source_span.quote` is non-empty. |
| `not_in_available_source` | The text that was read does not state the value. On `abstract_only` or `abstract_reclassify`, this means the abstract is silent. It does not mean the paper omitted the method. |
| `source_unavailable` | The text needed for this field could not be retrieved (no full text, failed parse, missing supplement). Distinct from silence inside a text you do have. |
| `not_applicable` | Reserved. Do not use it in gold annotation until Shawn has said this `field_id` does not apply to this design. |
| `uncertain` | The text contains competing wordings and no adjudicator has chosen. Put the competing passage in `source_span.quote`. Leave `raw_text` and `normalized_text` null. |
| `extraction_failed` | Text was available and the extractor returned nothing usable or schema-invalid output. Leave values null. |

For every code except `reported`, `raw_text`, `normalized_text`, and `raw_number` are `null`. A null is not a zero dose, an empty demographic category, or a “not reported” label inside `normalized_text`.

Worked shape (placeholders, not a real study):

```json
{
  "field_id": "route",
  "missingness": "not_in_available_source",
  "raw_text": null,
  "normalized_text": null,
  "raw_number": null,
  "experiment_id": null,
  "arm_id": null,
  "provenance": {
    "schema_version": "wp1-methods-draft-0.1",
    "extractor_id": "human-review",
    "model_id": null,
    "prompt_id": null,
    "confidence": null,
    "source_tier": "abstract_only",
    "source_span": {"quote": null, "section": null, "page": null, "char_start": null, "char_end": null},
    "human_override": true,
    "reviewed_at": "2026-09-22T00:00:00Z"
  }
}
```

## Multiple arms

If the source describes more than one exposure, write one assertion per arm and set `arm_id` to a local label (`arm-1`, `arm-2`). Do not average numbers and do not pick a “primary” dose unless an adjudicator has answered that question. If you cannot tell the arms apart, use `uncertain` on `dose_or_concentration` rather than a single blended value.

## SGBA+ — never infer

`sex_reported` and `gender_reported` are separate assertions. A sentence about one does not fill the other.

Fill `reported` only when the source text states the attribute. Allowed normalizations do not exist yet (`pending_biomedical`), so copy wording into `raw_text` and leave `normalized_text` null.

Leave the value null, with `not_in_available_source` or `source_unavailable`, when the source does not state it. Do not infer sex or gender from author names, participant names, pronouns guessed from context, geography, photographs, or the species of a model. Do not default animals or cells to a sex. The same rule applies to `age_reported`, `race_ethnicity_reported`, `indigenous_identity_reported`, `disability_reported`, `socioeconomic_reported`, and `geographic_context_reported`.

Existing `population_sex` values of `male`, `female`, or `both` are legacy extractor output. Do not copy them into `sex_reported` or `gender_reported` unless the source span still supports that exact claim, and do not write them into `gender_reported` at all.

## What the annotator does not decide

Hand the case to Shawn, unanswered, when the task is to choose a controlled term, a unit conversion, a sample-size denominator, a comparator, or an applicability rule. The register for those cases is `docs/methods/biomedical-adjudication-questions.md`.
